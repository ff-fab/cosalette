"""Command execution runner extracted from the App class.

Owns the per-command persistence stores and init-result cache, and
provides the six methods that wire, initialise, and dispatch
``@app.command`` handlers plus device command proxies.

This is Phase 4 of the COS-0fv decomposition epic.
"""

from __future__ import annotations

import asyncio
import functools
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
}


def _normalize_handler_return(
    func: Any,
    value: Any,
    state_model: type | None,
) -> dict[str, Any] | None:
    """Normalise a command handler return value to a JSON-compatible dict.

    Delegates to :func:`cosalette._contracts.normalize_handler_return`
    (shared helper, caches return annotation per function).
    """
    handler_name = getattr(func, "__qualname__", getattr(func, "__name__", None))
    return normalize_handler_return(func, value, state_model, handler_name=handler_name)


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
) -> None:
    """Invoke a command handler with error capture."""
    try:
        if _is_command_handler(handler):
            cmd = Command(
                topic=topic,
                payload=payload,
                sub_topic=sub_topic,
                timestamp=ctx.clock.now(),
            )
            await handler(cmd)
        else:
            await handler(sub_topic, payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "Device '%s' command handler error: %s",
            device_name,
            exc,
        )
        await publish_error_safely(error_publisher, exc, device_name, is_root)


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
            result = await reg.func(**kwargs)
            if result is not None:
                normalized = _normalize_handler_return(
                    reg.func, result, reg.state_model
                )
                if normalized is not None:
                    await ctx.publish_state(normalized)
            # Dispatch reactors after successful execution and state
            # publication. Reuse providers from handler invocation.
            if reactors:
                try:
                    from cosalette._wiring._reactors import dispatch_reactors

                    await dispatch_reactors(reactors, providers)
                except asyncio.CancelledError:
                    raise
                except Exception as reactor_exc:
                    # Route reactor failures through error publisher
                    # without rolling back the already-published result.
                    logger.error(
                        "Command '%s' reactor error: %s", reg.name, reactor_exc
                    )
                    await publish_error_safely(
                        error_publisher, reactor_exc, reg.name, reg.is_root
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
        cmd_ctx = ctx
        self.init_command_store(cmd_reg)
        await self.init_command_handler(cmd_reg, cmd_ctx, error_publisher)

        runner = self  # capture for closure

        async def _cmd_proxy(
            topic: str,
            payload: str,
            _reg: _CommandRegistration = cmd_reg,
            _ctx: DeviceContext = cmd_ctx,
            _ep: ErrorPublisher = error_publisher,
            _reactors: list[_ReactorRegistration] | None = reactors,
        ) -> None:
            await runner.run_command(_reg, _ctx, topic, payload, _ep, _reactors)

        router.register(
            cmd_reg.name,
            _cmd_proxy,
            is_root=cmd_reg.is_root,
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
                )
            elif _ctx._commands_consumed and sub_topic is None:
                cmd = Command(
                    topic=topic,
                    payload=payload,
                    timestamp=_ctx.clock.now(),
                )
                await _ctx._command_queue.put(cmd)
            else:
                logger.debug(
                    "Device '%s': no handler for sub-topic '%s'",
                    _name,
                    sub_topic,
                )

        router.register(
            reg.name,
            _proxy,
            is_root=reg.is_root,
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
                safe_sub = str(sub_value)[:64]
                exc = UnknownSubCommandError(
                    f"Unknown sub-command '{safe_sub}' for '{_group_name}'"
                )
                await publish_error_safely(_ep, exc, _group_name, _is_root)
                return

            await runner.run_command(reg, _ctx, topic, payload, _ep, _reactors)

        router.register(
            group_name,
            _sub_proxy,
            is_root=is_root,
        )
