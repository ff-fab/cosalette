"""Unit tests for telemetry freshness tracking and the ``stale`` status.

ADR-080: every telemetry entity records when it last completed a fresh
cycle.  A watchdog marks an entity whose last fresh cycle is older than its
``stale_after`` bound offline through the ``freshness`` availability source,
reports ``"stale"`` in the heartbeat, and the next fresh cycle recovers it.

Test Techniques Used:
    - Decision Table Testing: stale_after spec (unset/None/float/callable)
      x is_root x interval/cron in resolve_stale_after
    - Boundary Value Analysis: age == stale_after vs. just over it; invalid
      stale_after values at registration (0, negative, bool, non-finite)
    - State Transition Testing: fresh -> stale -> fresh, offline published once
    - Equivalence Partitioning: telemetry vs. non-telemetry heartbeat entries
    - Specification-based Testing: the pinned derived-default formula
    - Mock-based Isolation: MockMqttClient records publishes, FakeClock and
      ManualClock drive time
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from cosalette._app import App
from cosalette._cron import CronSchedule
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._mqtt import MqttNotConnectedError
from cosalette._registration import _UNSET, _TelemetryRegistration
from cosalette._router import Router
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette._strategies import OnChange
from cosalette._wiring import (
    resolve_stale_after,
    resolve_timeouts,
    start_freshness_task,
)
from cosalette._wiring._resolution import derive_stale_after
from cosalette.testing import (
    AppHarness,
    FakeClock,
    ManualClock,
    MockMqttClient,
    make_settings,
)

pytestmark = pytest.mark.unit

PREFIX = "myapp"
SENSOR_AVAILABILITY = f"{PREFIX}/sensor/availability"


class TransportError(Exception):
    """Stand-in for a downstream adapter error such as BleakError."""


@pytest.fixture
def clock() -> FakeClock:
    fake = FakeClock()
    fake._time = 0.0
    return fake


@pytest.fixture
def mock_mqtt() -> MockMqttClient:
    return MockMqttClient()


@pytest.fixture
def reporter(mock_mqtt: MockMqttClient, clock: FakeClock) -> HealthReporter:
    return HealthReporter(
        mqtt=mock_mqtt,
        topic_prefix=PREFIX,
        version="1.0.0",
        clock=clock,
    )


@pytest.fixture
def error_publisher(mock_mqtt: MockMqttClient) -> ErrorPublisher:
    return ErrorPublisher(mqtt=mock_mqtt, topic_prefix=PREFIX)


async def _never_called() -> dict[str, object] | None:
    """Handler stub: these tests drive the runner helpers directly."""
    return None  # pragma: no cover


def _reg(
    name: str = "sensor",
    *,
    interval: float | None = 60.0,
    schedule: CronSchedule | None = None,
    timeout: object = None,
    retry: int = 0,
    is_root: bool = False,
    stale_after: object = _UNSET,
) -> _TelemetryRegistration:
    return _TelemetryRegistration(
        name=name,
        func=_never_called,
        injection_plan=[],
        interval=interval,  # ty: ignore[invalid-argument-type]
        schedule=schedule,
        timeout=timeout,  # ty: ignore[invalid-argument-type]
        retry=retry,
        is_root=is_root,
        stale_after=stale_after,  # ty: ignore[invalid-argument-type]
    )


def _payloads(mock_mqtt: MockMqttClient, topic: str) -> list[str]:
    return [payload for payload, *_ in mock_mqtt.get_messages_for(topic)]


def _last_heartbeat(mock_mqtt: MockMqttClient) -> dict[str, Any]:
    return json.loads(_payloads(mock_mqtt, f"{PREFIX}/status")[-1])


# ---------------------------------------------------------------------------
# Derived default and resolution
# ---------------------------------------------------------------------------


class TestDeriveStaleAfter:
    """The derived default is pinned: ``2p + t(r+1) + 60r`` (ADR-080).

    Technique: Specification-based Testing — exact values for the formula.
    """

    @pytest.mark.parametrize(
        ("interval", "timeout", "retry", "expected"),
        [
            pytest.param(1500.0, 120.0, 3, 3660.0, id="retry-and-timeout"),
            pytest.param(60.0, None, 0, 120.0, id="no-timeout-no-retry"),
            pytest.param(10.0, 5.0, 0, 25.0, id="timeout-only"),
            pytest.param(10.0, None, 2, 140.0, id="retry-allowance-only"),
        ],
    )
    def test_interval_formula(
        self,
        interval: float,
        timeout: float | None,
        retry: int,
        expected: float,
    ) -> None:
        """The interval formula has no hidden factors or jitter."""
        reg = _reg(interval=interval, timeout=timeout, retry=retry)

        assert derive_stale_after(reg) == expected

    def test_cron_uses_the_regular_period(self) -> None:
        """A five-minute cron schedule derives two periods: 600 s."""
        reg = _reg(interval=None, schedule=CronSchedule("0 0/5 * * * ?"))

        assert derive_stale_after(reg) == 600.0

    def test_cron_uses_the_longest_gap(self) -> None:
        """An irregular schedule (08:00, 10:00) is bounded by its 22 h gap."""
        reg = _reg(interval=None, schedule=CronSchedule("0 0 8,10 * * ?"))

        assert derive_stale_after(reg) == 2 * 22 * 3600.0


class TestResolveStaleAfter:
    """Decision table for the ``stale_after`` spec at bootstrap.

    Technique: Decision Table Testing — spec kind x is_root.
    """

    @pytest.mark.parametrize(
        ("spec", "is_root", "expected"),
        [
            pytest.param(_UNSET, False, 120.0, id="unset-named-derives"),
            pytest.param(_UNSET, True, None, id="unset-root-disabled"),
            pytest.param(None, False, None, id="none-disables"),
            pytest.param(45.0, False, 45.0, id="explicit-kept"),
            pytest.param(45.0, True, 45.0, id="root-opts-in"),
            pytest.param(lambda s: 30.0, False, 30.0, id="callable-resolved"),
        ],
    )
    def test_resolution(self, spec: object, is_root: bool, expected: object) -> None:
        """Each spec kind resolves to a concrete float or None."""
        regs = [_reg(stale_after=spec, is_root=is_root)]

        resolve_stale_after(regs, make_settings())

        assert regs[0].stale_after == expected

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), True, "60"])
    def test_invalid_callable_result_raises(self, bad: object) -> None:
        """A callable resolving to a non-positive or non-number value fails."""
        regs = [_reg(stale_after=lambda s: bad)]

        with pytest.raises(ValueError, match="stale_after for 'sensor'"):
            resolve_stale_after(regs, make_settings())

    def test_default_reads_the_resolved_timeout(self) -> None:
        """Run after resolve_timeouts, the derived bound includes the timeout."""
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sensor", interval=10, timeout=4.0, retry=1)
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001
        resolve_stale_after(app._telemetry, settings)  # noqa: SLF001

        # 2*10 + 4*(1+1) + 60*1
        assert app._telemetry[0].stale_after == 88.0  # noqa: SLF001


# ---------------------------------------------------------------------------
# Registration plumbing and validation
# ---------------------------------------------------------------------------


class TestStaleAfterRegistration:
    """``stale_after`` reaches the registration on every registration path.

    Technique: Boundary Value Analysis + Error Guessing at registration.
    """

    def test_decorator_stores_value(self) -> None:
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sensor", interval=10, stale_after=90.0)
        async def sensor() -> dict[str, object]:
            return {}

        assert app._telemetry[0].stale_after == 90.0  # noqa: SLF001

    def test_omitted_stores_unset(self) -> None:
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sensor", interval=10)
        async def sensor() -> dict[str, object]:
            return {}

        assert app._telemetry[0].stale_after is _UNSET  # noqa: SLF001

    def test_add_telemetry_stores_none(self) -> None:
        app = App(name="testapp", version="1.0.0")

        async def handler() -> dict[str, object]:
            return {}

        app.add_telemetry("sensor", handler, interval=10, stale_after=None)

        assert app._telemetry[0].stale_after is None  # noqa: SLF001

    def test_router_value_survives_include_router(self) -> None:
        app = App(name="testapp", version="1.0.0")
        router = Router(prefix="sensors")

        @router.telemetry("dev", interval=10, stale_after=75.0)
        async def handler() -> dict[str, object]:
            return {}

        app.include_router(router)

        assert app._telemetry[0].name == "sensors/dev"  # noqa: SLF001
        assert app._telemetry[0].stale_after == 75.0  # noqa: SLF001

    @pytest.mark.parametrize(
        "bad",
        [0, -5.0, True, float("inf"), float("nan")],
        ids=["zero", "negative", "bool", "inf", "nan"],
    )
    def test_decorator_rejects_invalid(self, bad: object) -> None:
        app = App(name="testapp", version="1.0.0")

        with pytest.raises(ValueError, match="stale_after"):

            @app.telemetry("sensor", interval=10, stale_after=bad)  # ty: ignore[invalid-argument-type]
            async def sensor() -> dict[str, object]:
                return {}

    def test_add_telemetry_rejects_invalid(self) -> None:
        app = App(name="testapp", version="1.0.0")

        async def handler() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="stale_after"):
            app.add_telemetry("sensor", handler, interval=10, stale_after=0)

    def test_router_rejects_invalid(self) -> None:
        router = Router()

        with pytest.raises(ValueError, match="stale_after"):

            @router.telemetry("dev", interval=10, stale_after=-1.0)
            async def handler() -> dict[str, object]:
                return {}


# ---------------------------------------------------------------------------
# Reporter state machine
# ---------------------------------------------------------------------------


class TestFreshnessTransitions:
    """fresh -> stale -> fresh on the reporter, driven by a FakeClock.

    Technique: State Transition Testing + Boundary Value Analysis.
    """

    async def test_not_stale_at_exactly_stale_after(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """An age equal to the bound is still fresh."""
        reporter.track_freshness("sensor", 100.0)
        clock.advance(100.0)

        await reporter.check_freshness()

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == []
        assert not reporter.is_unavailable("sensor")

    async def test_stale_just_over_stale_after(
        self,
        reporter: HealthReporter,
        clock: FakeClock,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Just past the bound the entity is marked offline with one WARNING."""
        reporter.track_freshness("sensor", 100.0)
        reporter.record_failure("sensor", TransportError("gone"))
        clock.advance(100.5)

        with caplog.at_level(logging.WARNING, logger="cosalette._health"):
            await reporter.check_freshness()

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == ["offline"]
        assert reporter.is_unavailable("sensor", source="freshness")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "stale" in warnings[0].getMessage()
        assert "TransportError" in warnings[0].getMessage()

    async def test_offline_published_once_while_stale(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """Repeated checks of a stale entity do not republish."""
        reporter.track_freshness("sensor", 10.0)
        for _ in range(3):
            clock.advance(20.0)
            await reporter.check_freshness()

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == ["offline"]

    async def test_fresh_cycle_recovers(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """The next fresh cycle publishes online and restarts the clock."""
        reporter.track_freshness("sensor", 10.0)
        clock.advance(11.0)
        await reporter.check_freshness()

        await reporter.record_success("sensor")
        clock.advance(5.0)
        await reporter.check_freshness()

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == ["offline", "online"]
        assert not reporter.is_unavailable("sensor")

    async def test_success_while_fresh_publishes_nothing(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """A fresh cycle on a fresh entity is not an availability transition."""
        reporter.track_freshness("sensor", 10.0)

        await reporter.record_success("sensor")

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == []

    async def test_stale_after_none_never_goes_stale(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """``stale_after=None`` is a no-op for the watchdog."""
        reporter.track_freshness("sensor", None)
        clock.advance(1e9)

        await reporter.check_freshness()

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == []
        assert reporter.min_stale_after() is None

    async def test_root_entity_uses_app_availability_topic(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """A root entity that opted in goes stale on the app-wide topic."""
        reporter.track_freshness("root", 10.0, is_root=True)
        clock.advance(11.0)

        await reporter.check_freshness()

        assert _payloads(mock_mqtt, f"{PREFIX}/availability") == ["offline"]

    async def test_freshness_recovery_respects_other_sources(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """Clearing freshness does not override a still-active telemetry mark."""
        reporter.track_freshness("sensor", 10.0)
        await reporter.publish_device_unavailable("sensor", source="telemetry")
        clock.advance(11.0)
        await reporter.check_freshness()

        await reporter.record_success("sensor")

        # Already offline via telemetry: no duplicate, and no premature online.
        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == ["offline"]
        assert reporter.is_unavailable("sensor", source="telemetry")
        assert not reporter.is_unavailable("sensor", source="freshness")

    def test_untracked_entity_is_ignored(self, reporter: HealthReporter) -> None:
        """Counting on an untracked device is a no-op, not an error."""
        reporter.record_failure("ghost", TransportError("x"))

        assert reporter.min_stale_after() is None


# ---------------------------------------------------------------------------
# Heartbeat payload
# ---------------------------------------------------------------------------


class TestHeartbeatFreshness:
    """Freshness fields and the ``stale`` status in the heartbeat JSON.

    Technique: Equivalence Partitioning — tracked vs. untracked devices.
    """

    async def test_stale_outranks_error(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """A stale entity reports ``stale`` even while its status is error."""
        reporter.set_device_status("sensor", "error")
        reporter.track_freshness("sensor", 10.0)
        reporter.record_failure("sensor", TransportError("gone"))
        reporter.record_failure("sensor", TransportError("gone"))
        clock.advance(11.0)
        await reporter.check_freshness()

        await reporter.publish_heartbeat()

        entry = _last_heartbeat(mock_mqtt)["devices"]["sensor"]
        assert entry == {
            "status": "stale",
            "last_success_at": None,
            "consecutive_failures": 2,
        }

    async def test_success_records_timestamp_and_resets_counter(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """A fresh cycle sets ``last_success_at`` and zeroes the counter."""
        reporter.set_device_status("sensor")
        reporter.track_freshness("sensor", 10.0)
        reporter.record_failure("sensor", TransportError("gone"))

        await reporter.record_success("sensor")
        await reporter.publish_heartbeat()

        entry = _last_heartbeat(mock_mqtt)["devices"]["sensor"]
        assert entry["status"] == "ok"
        assert entry["consecutive_failures"] == 0
        assert entry["last_success_at"].endswith("+00:00")

    async def test_non_telemetry_device_has_no_freshness_fields(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """Devices outside the telemetry archetype keep the plain shape."""
        reporter.set_device_status("valve")

        await reporter.publish_heartbeat()

        assert _last_heartbeat(mock_mqtt)["devices"]["valve"] == {"status": "ok"}


# ---------------------------------------------------------------------------
# Runner integration points
# ---------------------------------------------------------------------------


class TestRunnerHooks:
    """The telemetry runner's success and failure paths feed freshness.

    Technique: Specification-based Testing on the runner helpers.
    """

    async def test_handler_error_counts_failure(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        reporter.set_device_status("sensor")
        reporter.track_freshness("sensor", 10.0)

        await TelemetryRunner._handle_telemetry_error(
            _reg(), TransportError("gone"), None, error_publisher, reporter
        )
        await reporter.publish_heartbeat()

        entry = _last_heartbeat(mock_mqtt)["devices"]["sensor"]
        assert entry["consecutive_failures"] == 1

    async def test_mqtt_not_connected_is_not_a_failure(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """A broker outage is a transport condition, not a handler failure."""
        reporter.set_device_status("sensor")
        reporter.track_freshness("sensor", 10.0)

        await TelemetryRunner._handle_telemetry_error(
            _reg(), MqttNotConnectedError("down"), None, error_publisher, reporter
        )
        await reporter.publish_heartbeat()

        entry = _last_heartbeat(mock_mqtt)["devices"]["sensor"]
        assert entry["consecutive_failures"] == 0

    async def test_clear_error_records_success(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """The success path is the freshness mark, clearing a stale offline."""
        reporter.track_freshness("sensor", 10.0)
        clock.advance(11.0)
        await reporter.check_freshness()

        await TelemetryRunner._clear_telemetry_error("sensor", None, reporter)

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == ["offline", "online"]


class TestStartFreshnessTask:
    """The watchdog starts only when some entity has a bound.

    Technique: Decision Table Testing — resolved bound present or not.
    """

    async def test_no_task_without_any_bound(self, reporter: HealthReporter) -> None:
        """All-disabled (or unresolved) bounds start no watchdog."""
        regs = [_reg("a", stale_after=None), _reg("b", stale_after=_UNSET)]

        task = start_freshness_task(regs, 60.0, reporter)

        assert task is None
        assert reporter.min_stale_after() is None

    async def test_task_started_and_entities_tracked(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """Every entity is tracked; one bound is enough to start the task."""
        regs = [_reg("a", stale_after=None), _reg("b", stale_after=30.0)]

        task = start_freshness_task(regs, None, reporter)
        try:
            assert task is not None
            assert reporter.min_stale_after() == 30.0
            reporter.set_device_status("a")
            await reporter.publish_heartbeat()
            entry = _last_heartbeat(mock_mqtt)["devices"]["a"]
            assert entry["consecutive_failures"] == 0
        finally:
            assert task is not None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


# ---------------------------------------------------------------------------
# End to end through the App
# ---------------------------------------------------------------------------

AVAILABILITY = "testapp/sensor/availability"


async def _run_for(
    handler_factory: Any, *, seconds: float, step: float = 10.0, **kwargs: Any
) -> list[str]:
    """Run one telemetry entity for *seconds* of virtual time.

    Returns the availability payloads published before shutdown.
    """
    clock = ManualClock()
    harness = AppHarness.create(clock=clock)
    harness.app.telemetry("sensor", interval=10, **kwargs)(handler_factory())

    task = asyncio.create_task(harness.run())
    try:
        await harness.wait_for_publish_count(AVAILABILITY, 1)
        elapsed = 0.0
        while elapsed < seconds:
            await harness.advance_time(step)
            elapsed += step
        await clock.settle()
        return _payloads(harness.mqtt, AVAILABILITY)
    finally:
        harness.trigger_shutdown()
        await task


class TestFreshnessEndToEnd:
    """The watchdog runs inside the app lifecycle.

    Technique: State Transition Testing through the real wiring.
    """

    async def test_failure_outside_unavailable_on_goes_stale(self) -> None:
        """An error the transport mark ignores is still caught by freshness."""

        def factory() -> Any:
            async def sensor() -> dict[str, int]:
                raise ValueError("bad parse")

            return sensor

        payloads = await _run_for(
            factory,
            seconds=60,
            stale_after=30.0,
            unavailable_on=(TransportError,),
        )

        assert payloads == ["online", "offline"]

    async def test_suppressed_values_count_as_fresh(self) -> None:
        """A value the PublishStrategy suppresses is still a fresh cycle."""

        def factory() -> Any:
            async def sensor() -> dict[str, int]:
                return {"value": 1}

            return sensor

        payloads = await _run_for(
            factory,
            seconds=120,
            stale_after=30.0,
            publish=OnChange(),
        )

        assert payloads == ["online"]

    async def test_opt_out_never_goes_stale(self) -> None:
        """``stale_after=None`` disables the watchdog for the entity."""

        def factory() -> Any:
            async def sensor() -> dict[str, int]:
                raise ValueError("bad parse")

            return sensor

        payloads = await _run_for(
            factory,
            seconds=120,
            stale_after=None,
            unavailable_on=(TransportError,),
        )

        assert payloads == ["online"]
