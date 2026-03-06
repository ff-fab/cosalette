"""Command execution runner extracted from the App class.

Owns the per-command persistence stores and init-result cache, and
provides the six methods that wire, initialise, and dispatch
``@app.command`` handlers plus device command proxies.

This is Phase 4 of the COS-0fv decomposition epic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._injection import build_providers, resolve_kwargs
from cosalette._registration import (
    _call_init,
    _CommandRegistration,
    _DeviceRegistration,
)
from cosalette._router import TopicRouter
from cosalette._runner_utils import (
    create_device_store,
    publish_error_safely,
    save_store_on_shutdown,
)
from cosalette._stores import DeviceStore, Store

logger = logging.getLogger(__name__)


class CommandRunner:
    """Encapsulates command execution state and wiring.

    Constructed once per ``_run_async`` invocation with the
    application's optional :class:`Store` backend.  Owns:

    * ``_command_init_results`` — cached ``init=`` callback results
    * ``_command_stores`` — per-command :class:`DeviceStore` instances
    """

    def __init__(self, store: Store | None) -> None:
        self._store = store
        self._command_init_results: dict[str, Any] = {}
        self._command_stores: dict[str, DeviceStore] = {}

    # -- public helpers -----------------------------------------------------

    def prepare_command_kwargs(
        self,
        reg: _CommandRegistration,
        ctx: DeviceContext,
        topic: str,
        payload: str,
    ) -> dict[str, Any]:
        """Build the resolved kwargs for a command handler."""
        providers = build_providers(ctx, reg.name)
        if reg.name in self._command_init_results:
            cached = self._command_init_results[reg.name]
            providers[type(cached)] = cached
        if reg.name in self._command_stores:
            providers[DeviceStore] = self._command_stores[reg.name]
        kwargs = resolve_kwargs(reg.injection_plan, providers)
        if "topic" in reg.mqtt_params:
            kwargs["topic"] = topic
        if "payload" in reg.mqtt_params:
            kwargs["payload"] = payload
        return kwargs

    async def run_command(
        self,
        reg: _CommandRegistration,
        ctx: DeviceContext,
        topic: str,
        payload: str,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Dispatch a single command to a ``@app.command`` handler."""
        try:
            kwargs = self.prepare_command_kwargs(reg, ctx, topic, payload)
            result = await reg.func(**kwargs)
            if result is not None:
                await ctx.publish_state(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Command handler '%s' error: %s", reg.name, exc)
            await publish_error_safely(error_publisher, exc, reg.name, reg.is_root)
        finally:
            save_store_on_shutdown(self._command_stores.get(reg.name), reg.name)

    def init_command_store(
        self,
        cmd_reg: _CommandRegistration,
    ) -> DeviceStore | None:
        """Create a per-device store for a command handler.

        Returns the store when persistence is enabled, otherwise ``None``.
        """
        if self._store is not None:
            store = create_device_store(self._store, cmd_reg.name)
            self._command_stores[cmd_reg.name] = store
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
        if cmd_reg.init is not None:
            cmd_providers = build_providers(ctx, cmd_reg.name)
            if cmd_reg.name in self._command_stores:
                cmd_providers[DeviceStore] = self._command_stores[cmd_reg.name]
            try:
                init_result = _call_init(
                    cmd_reg.init, cmd_reg.init_injection_plan, cmd_providers
                )
                self._command_init_results[cmd_reg.name] = init_result
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
        if cmd_reg.name in self._command_stores:
            cmd_st = self._command_stores[cmd_reg.name]
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
        ) -> None:
            await runner.run_command(_reg, _ctx, topic, payload, _ep)

        router.register(cmd_reg.name, _cmd_proxy, is_root=cmd_reg.is_root)

    @staticmethod
    def register_device_proxy(
        reg: _DeviceRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        router: TopicRouter,
    ) -> None:
        """Create a command-handler proxy for a device and register it."""
        dev_ctx = ctx

        async def _proxy(
            topic: str,
            payload: str,
            _ctx: DeviceContext = dev_ctx,
            _ep: ErrorPublisher = error_publisher,
            _name: str = reg.name,
            _is_root: bool = reg.is_root,
        ) -> None:
            handler = _ctx.command_handler
            if handler is not None:
                try:
                    await handler(topic, payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "Device '%s' command handler error: %s",
                        _name,
                        exc,
                    )
                    await publish_error_safely(_ep, exc, _name, _is_root)

        router.register(reg.name, _proxy, is_root=reg.is_root)
