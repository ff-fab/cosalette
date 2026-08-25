"""Command execution runner.

Owns the per-command persistence stores and init-result cache, and
provides the methods that wire, initialise, and dispatch
``@app.command`` handlers plus device command proxies.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
import logging
import typing
from typing import Any

from cosalette._command import Command
from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._injection import build_providers, resolve_request_kwargs
from cosalette._mqtt import CommandHandler
from cosalette._mqtt._router import TopicRouter
from cosalette._persistence._stores import DeviceStore, Store
from cosalette._registration import (
    _call_init,
    _CommandRegistration,
    _DeviceRegistration,
    _ReactorRegistration,
)
from cosalette._runners._contracts import normalize_handler_return
from cosalette._runners._runner_utils import (
    create_device_store,
    publish_error_safely,
    save_store_on_shutdown,
)
from cosalette._utils import _DEFAULT_COMMAND_TIMEOUT

logger = logging.getLogger(__name__)


class InvalidJsonError(Exception):
    """Raised when command payload is not valid JSON."""


class MissingSubKeyError(Exception):
    """Raised when sub-command payload missing required routing key."""


class UnknownSubCommandError(Exception):
    """Raised when sub-command value is not recognized."""


_FRAMEWORK_ERROR_TYPE_MAP: dict[type[Exception], str] = {
    InvalidJsonError: "invalid_json",
    MissingSubKeyError: "missing_sub_key",
    UnknownSubCommandError: "unknown_sub_command",
    # Watchdog cancellation (ADR-060); matches the documented taxonomy
    # (reference/errors.md) which already promised this mapping.
    TimeoutError: "timeout",
}


@functools.lru_cache(maxsize=64)
def _is_command_handler(handler: CommandHandler) -> bool:
    """Return True if *handler* expects a :class:`Command` object (new-style).

    Inspects the type annotation of the first parameter. If annotated
    as ``Command``, the handler is new-style and receives a single
    ``Command`` instance instead of ``(sub_topic, payload)``.

    Cached per handler — reflection is done once, not per message.
    """
    try:
        hints = typing.get_type_hints(handler)
    except Exception:
        return False
    params = list(inspect.signature(handler).parameters.keys())
    if not params:
        return False
    return hints.get(params[0]) is Command


def _extract_sub_topic(topic: str, base: str) -> str | None:
    """Parse the sub-topic segment from a command topic string."""
    suffix = "/set"
    relative = topic[len(base) : -len(suffix)] if topic.endswith(suffix) else ""
    if relative == "":
        return None
    if relative.startswith("/"):
        return relative[1:]
    return None


async def _dispatch_handler(
    handler: CommandHandler,
    topic: str,
    payload: str,
    sub_topic: str | None,
    ctx: DeviceContext,
    error_publisher: ErrorPublisher,
    device_name: str,
    is_root: bool,
    timeout: float | None = _DEFAULT_COMMAND_TIMEOUT,
) -> None:
    """Invoke a command handler with watchdog and error capture (ADR-060)."""
    try:
        if _is_command_handler(handler):
            coro = handler(
                Command(
                    topic=topic,
                    payload=payload,
                    sub_topic=sub_topic,
                    timestamp=ctx.clock.now(),
                )
            )
        else:
            coro = handler(sub_topic, payload)
        if timeout is not None:
            async with asyncio.timeout(timeout):
                await coro
        else:
            await coro
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        logger.error(
            "Device '%s' command handler exceeded %.1fs watchdog; cancelled",
            device_name,
            timeout,
        )
        await publish_error_safely(
            error_publisher, TimeoutError(), device_name, is_root
        )
    except Exception as exc:
        logger.error(
            "Device '%s' command handler error: %s",
            device_name,
            exc,
        )
        await publish_error_safely(error_publisher, exc, device_name, is_root)


def _validate_sub_command_group(group: list[_CommandRegistration]) -> None:
    """Raise ValueError if group members disagree on maxsize or backpressure."""
    if len(group) <= 1:
        return
    first_maxsize = group[0].maxsize
    first_backpressure = group[0].backpressure
    mismatched = [
        r
        for r in group[1:]
        if r.maxsize != first_maxsize or r.backpressure != first_backpressure
    ]
    if mismatched:
        names = [r.sub for r in mismatched]
        msg = (
            f"Sub-commands {names!r} of '{group[0].name}' have different "
            f"maxsize/backpressure than '{group[0].sub}'. All sub-commands "
            f"in a group must share the same queue settings."
        )
        raise ValueError(msg)


class CommandRunner:
    """Encapsulates command execution state and wiring.

    Constructed once per ``_run_async`` invocation with the
    application's optional :class:`Store` backend.  Owns:

    * ``_command_init_results`` — cached ``init=`` callback results
    * ``_command_stores`` — per-command :class:`DeviceStore` instances
    """

    def __init__(self, store: Store | None) -> None:
        self._store = store
        # Keyed by (name, sub) to support multiple sub-dispatch handlers sharing
        # the same topic name without cache collision.
        self._command_init_results: dict[tuple[str, str | None], Any] = {}
        self._command_stores: dict[tuple[str, str | None], DeviceStore] = {}

    # -- public helpers -----------------------------------------------------

    def prepare_command_kwargs(
        self,
        reg: _CommandRegistration,
        ctx: DeviceContext,
        topic: str,
        payload: str,
    ) -> tuple[dict[str, Any], dict[type, Any]]:
        """Build the resolved kwargs and providers for a command handler.

        Returns (kwargs, providers) tuple to allow provider reuse.
        """
        providers = build_providers(ctx, reg.name, reg.per_device_config)
        _reg_key = (reg.name, reg.sub)
        if _reg_key in self._command_init_results:
            cached = self._command_init_results[_reg_key]
            providers[type(cached)] = cached
        if _reg_key in self._command_stores:
            providers[DeviceStore] = self._command_stores[_reg_key]
        kwargs = resolve_request_kwargs(
            reg.injection_plan, providers, topic=topic, payload=payload
        )
        if "topic" in reg.mqtt_params:
            kwargs["topic"] = topic
        if "payload" in reg.mqtt_params:
            kwargs["payload"] = payload
        return kwargs, providers

    async def _auto_recover_if_needed(self, ctx: DeviceContext) -> None:
        """Publish 'online' if the device was previously marked unavailable."""
        if ctx._is_unavailable and ctx._health_reporter is not None:
            await ctx._health_reporter.publish_device_available(
                ctx._name, is_root=ctx._is_root
            )
            ctx._is_unavailable = False

    async def _dispatch_reactors_safely(
        self,
        reg: _CommandRegistration,
        providers: dict[type, object],
        error_publisher: ErrorPublisher,
        reactors: list[_ReactorRegistration],
    ) -> None:
        """Run reactors after a successful handler invocation."""
        try:
            from cosalette._wiring._reactors import dispatch_reactors

            await dispatch_reactors(reactors, providers)
        except asyncio.CancelledError:
            raise
        except Exception as reactor_exc:
            logger.error("Command '%s' reactor error: %s", reg.name, reactor_exc)
            await publish_error_safely(
                error_publisher, reactor_exc, reg.name, reg.is_root
            )

    async def _invoke_handler(
        self,
        reg: _CommandRegistration,
        ctx: DeviceContext,
        kwargs: dict[str, object],
        providers: dict[type, object],
        error_publisher: ErrorPublisher,
        reactors: list[_ReactorRegistration] | None,
    ) -> None:
        """Invoke handler, handle unavailability, auto-recover, and run reactors."""
        try:
            coro = reg.func(**kwargs)
            if isinstance(reg.timeout, (int, float)) and not isinstance(
                reg.timeout, bool
            ):
                async with asyncio.timeout(reg.timeout):
                    result = await coro
            else:
                result = await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if reg.unavailable_on and isinstance(exc, tuple(reg.unavailable_on)):
                await ctx.mark_unavailable()
                await publish_error_safely(error_publisher, exc, reg.name, reg.is_root)
                return
            raise

        if result is not None:
            handler_name = getattr(
                reg.func, "__qualname__", getattr(reg.func, "__name__", None)
            )
            normalized = normalize_handler_return(
                reg.func, result, reg.state_model, handler_name=handler_name
            )
            if normalized is not None:
                await ctx.publish_state(normalized)

        await self._auto_recover_if_needed(ctx)

        if reactors:
            await self._dispatch_reactors_safely(
                reg, providers, error_publisher, reactors
            )

    async def run_command(
        self,
        reg: _CommandRegistration,
        ctx: DeviceContext,
        topic: str,
        payload: str,
        error_publisher: ErrorPublisher,
        reactors: list[_ReactorRegistration] | None = None,
    ) -> None:
        """Dispatch a single command to a ``@app.command`` handler."""
        try:
            kwargs, providers = self.prepare_command_kwargs(reg, ctx, topic, payload)
            await self._invoke_handler(
                reg, ctx, kwargs, providers, error_publisher, reactors
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Command handler '%s' error: %s", reg.name, exc)
            await publish_error_safely(error_publisher, exc, reg.name, reg.is_root)
        finally:
            save_store_on_shutdown(
                self._command_stores.get((reg.name, reg.sub)), reg.name
            )

    def init_command_store(
        self,
        cmd_reg: _CommandRegistration,
    ) -> DeviceStore | None:
        """Create a per-device store for a command handler.

        Returns the store when persistence is enabled, otherwise ``None``.
        """
        if self._store is not None:
            store = create_device_store(self._store, cmd_reg.name)
            self._command_stores[(cmd_reg.name, cmd_reg.sub)] = store
            return store
        return None

    async def init_command_handler(
        self,
        cmd_reg: _CommandRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Run the optional init callback for a command handler.

        Caches the result in ``_command_init_results``.  If init fails the
        error is logged and published safely.  If the store is dirty after init
        it is flushed.
        """
        _reg_key = (cmd_reg.name, cmd_reg.sub)
        if cmd_reg.init is not None:
            cmd_providers = build_providers(
                ctx,
                cmd_reg.name,
                cmd_reg.per_device_config,
            )
            if _reg_key in self._command_stores:
                cmd_providers[DeviceStore] = self._command_stores[_reg_key]
            try:
                init_result = _call_init(
                    cmd_reg.init, cmd_reg.init_injection_plan, cmd_providers
                )
                self._command_init_results[_reg_key] = init_result
            except Exception as exc:
                logger.error(
                    "Command '%s' init= callback failed: %s",
                    cmd_reg.name,
                    exc,
                )
                await publish_error_safely(
                    error_publisher, exc, cmd_reg.name, cmd_reg.is_root
                )

        # Flush store if init= mutated it
        if _reg_key in self._command_stores:
            cmd_st = self._command_stores[_reg_key]
            if cmd_st.dirty:
                try:
                    cmd_st.save()
                except Exception:
                    logger.exception(
                        "Failed to save store after init= for command '%s'",
                        cmd_reg.name,
                    )

    async def register_command_proxy(
        self,
        cmd_reg: _CommandRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        router: TopicRouter,
        reactors: list[_ReactorRegistration] | None = None,
    ) -> None:
        """Orchestrate command store init, handler init, and proxy registration."""
        self.init_command_store(cmd_reg)
        await self.init_command_handler(cmd_reg, ctx, error_publisher)

        runner = self  # capture for closure

        async def _cmd_proxy(
            topic: str,
            payload: str,
            _reg: _CommandRegistration = cmd_reg,
            _ctx: DeviceContext = ctx,
            _ep: ErrorPublisher = error_publisher,
            _reactors: list[_ReactorRegistration] | None = reactors,
        ) -> None:
            await runner.run_command(_reg, _ctx, topic, payload, _ep, _reactors)

        router.register(
            cmd_reg.name,
            _cmd_proxy,
            is_root=cmd_reg.is_root,
            maxsize=cmd_reg.maxsize,
            backpressure=cmd_reg.backpressure,
        )

    @staticmethod
    def register_device_proxy(
        reg: _DeviceRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        router: TopicRouter,
    ) -> None:
        """Create a command-handler proxy for a device and register it."""
        dev_ctx = ctx
        topic_base = ctx._topic_base

        async def _proxy(
            topic: str,
            payload: str,
            _ctx: DeviceContext = dev_ctx,
            _ep: ErrorPublisher = error_publisher,
            _name: str = reg.name,  # post-expansion: always str
            _is_root: bool = reg.is_root,
            _base: str = topic_base,
        ) -> None:
            sub_topic = _extract_sub_topic(topic, _base)
            handler = _ctx.get_command_handler(sub_topic)
            if handler is not None:
                await _dispatch_handler(
                    handler,
                    topic,
                    payload,
                    sub_topic,
                    _ctx,
                    _ep,
                    _name,
                    _is_root,
                    timeout=_ctx.get_command_timeout(sub_topic),
                )
            elif _ctx._commands_consumed and sub_topic is None:
                cmd = Command(
                    topic=topic,
                    payload=payload,
                    timestamp=_ctx.clock.now(),
                )
                _ctx._enqueue_command(cmd)
            else:
                logger.debug(
                    "Device '%s': no handler for sub-topic %r",
                    _name,
                    sub_topic,
                )

        router.register(
            reg.name,
            _proxy,
            is_root=reg.is_root,
            maxsize=reg.maxsize,
            backpressure=reg.backpressure,
        )

    async def register_sub_command_proxy(
        self,
        group: list[_CommandRegistration],
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        router: TopicRouter,
        reactors: list[_ReactorRegistration] | None = None,
    ) -> None:
        """Register a single proxy for a group of sub-command handlers."""
        if not group:
            return

        _validate_sub_command_group(group)

        group_name = group[0].name
        is_root = group[0].is_root
        sub_key = group[0].sub_key

        # Initialize stores and handlers for each registration
        for reg in group:
            self.init_command_store(reg)
            await self.init_command_handler(reg, ctx, error_publisher)

        # Build handlers_by_sub mapping
        handlers_by_sub: dict[str, _CommandRegistration] = {}
        for reg in group:
            if reg.sub is not None:  # Should always be true, but defense
                handlers_by_sub[reg.sub] = reg

        runner = self  # capture for closure

        async def _sub_proxy(
            topic: str,
            payload: str,
            _group_name: str = group_name,
            _is_root: bool = is_root,
            _sub_key: str = sub_key,
            _handlers: dict[str, _CommandRegistration] = handlers_by_sub,
            _ctx: DeviceContext = ctx,
            _ep: ErrorPublisher = error_publisher,
            _reactors: list[_ReactorRegistration] | None = reactors,
        ) -> None:
            try:
                data = json.loads(payload)
            except RecursionError:
                # Deeply nested payloads (within the inbound size cap) can blow
                # the interpreter recursion limit; surface as invalid JSON
                # instead of leaking an unstructured error (CWE-674).
                wrapped_exc = InvalidJsonError("payload nesting too deep")
                await publish_error_safely(_ep, wrapped_exc, _group_name, _is_root)
                return
            except json.JSONDecodeError as exc:
                wrapped_exc = InvalidJsonError(str(exc))
                await publish_error_safely(_ep, wrapped_exc, _group_name, _is_root)
                return

            if not isinstance(data, dict):
                wrapped_exc = InvalidJsonError(
                    f"Command payload must be a JSON object, got {type(data).__name__}"
                )
                await publish_error_safely(_ep, wrapped_exc, _group_name, _is_root)
                return

            sub_value = data.get(_sub_key)
            if sub_value is None:
                exc = MissingSubKeyError(
                    f"Missing field '{_sub_key}' in command payload"
                )
                await publish_error_safely(_ep, exc, _group_name, _is_root)
                return

            reg = _handlers.get(str(sub_value))
            if reg is None:
                # Echo only a non-reversible fingerprint of the value: raw
                # slices could leak secret-bearing payload fragments (e.g.
                # token prefixes) onto error topics (CWE-209). The full value
                # stays in the local log under the correlation id.
                sub_repr = str(sub_value)
                fingerprint = hashlib.sha256(sub_repr.encode()).hexdigest()[:8]
                exc = UnknownSubCommandError(
                    f"Unknown sub-command for '{_group_name}' "
                    f"(type={type(sub_value).__name__}, len={len(sub_repr)}, "
                    f"fp={fingerprint})"
                )
                logger.warning(
                    "Unknown sub-command %r for '%s'",
                    sub_repr,
                    _group_name,
                )
                await publish_error_safely(_ep, exc, _group_name, _is_root)
                return

            await runner.run_command(reg, _ctx, topic, payload, _ep, _reactors)

        router.register(
            group_name,
            _sub_proxy,
            is_root=is_root,
            maxsize=group[0].maxsize,
            backpressure=group[0].backpressure,
        )
