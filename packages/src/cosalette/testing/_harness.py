"""Test harness wrapping App with pre-configured test doubles.

Provides :class:`AppHarness` — a one-liner setup for integration-style
tests that eliminates the boilerplate of creating App, MockMqttClient,
FakeClock, Settings, and an ``asyncio.Event`` individually.

See Also:
    ADR-007 for testing strategy decisions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Self

from cosalette._app import App
from cosalette._mqtt import MockMqttClient
from cosalette._settings import Settings
from cosalette.testing._clock import FakeClock
from cosalette.testing._settings import make_settings


@dataclass
class AppHarness:
    """Test harness wrapping App with pre-configured test doubles.

    Provides unified access to App, MockMqttClient, FakeClock,
    Settings, and a shutdown Event — eliminating boilerplate in
    integration-style tests.

    Usage::

        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx):
            ...

        # Run with auto-shutdown after device_called event:
        await harness.run()

    See Also:
        ADR-007 for testing strategy decisions.
    """

    app: App
    mqtt: MockMqttClient
    clock: FakeClock
    settings: Settings
    shutdown_event: asyncio.Event

    @classmethod
    def create(
        cls,
        *,
        name: str = "testapp",
        version: str = "1.0.0",
        dry_run: bool = False,
        **settings_overrides: Any,
    ) -> Self:
        """Create a harness with fresh test doubles.

        Args:
            name: App name.
            version: App version.
            dry_run: When True, forward to App for dry-run adapter variants.
            **settings_overrides: Forwarded to :func:`make_settings`.

        Returns:
            A fully wired :class:`AppHarness` ready for test use.
        """
        return cls(
            app=App(name=name, version=version, dry_run=dry_run),
            mqtt=MockMqttClient(),
            clock=FakeClock(),
            settings=make_settings(**settings_overrides),
            shutdown_event=asyncio.Event(),
        )

    async def run(self) -> None:
        """Run ``_run_async`` with the harness's test doubles."""
        await self.app._run_async(
            settings=self.settings,
            shutdown_event=self.shutdown_event,
            mqtt=self.mqtt,
            clock=self.clock,
        )

    def trigger_shutdown(self) -> None:
        """Signal the shutdown event."""
        self.shutdown_event.set()
