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
    - Equivalence Partitioning: telemetry vs. non-telemetry heartbeat entries;
      dict-name (per-device config) vs. list-name (settings) callables
    - Specification-based Testing: the pinned derived-default formula
    - Boundary Value Analysis: backoff max_delay below, at and above the
      60 s per-retry allowance floor
    - Mock-based Isolation: MockMqttClient records publishes, FakeClock and
      ManualClock drive time
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from cosalette._app import App
from cosalette._cron import CronSchedule
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._mqtt import MqttNotConnectedError
from cosalette._registration import _UNSET, _TelemetryRegistration
from cosalette._retry import ExponentialBackoff, FixedBackoff, LinearBackoff
from cosalette._router import Router
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette._settings import Settings
from cosalette._strategies import OnChange
from cosalette._wiring import (
    _expand_telemetry_names,
    resolve_intervals,
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
    backoff: object = None,
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
        backoff=backoff,  # ty: ignore[invalid-argument-type]
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
    """The derived default is pinned: ``2p + t(r+1) + max(60, cap) r`` (ADR-080).

    Technique: Specification-based Testing — exact values for the formula.
    """

    @pytest.mark.parametrize(
        ("interval", "timeout", "retry", "expected"),
        [
            pytest.param(1500.0, 120.0, 3, 3696.0, id="retry-and-timeout"),
            pytest.param(60.0, None, 0, 120.0, id="no-timeout-no-retry"),
            pytest.param(10.0, 5.0, 0, 25.0, id="timeout-only"),
            pytest.param(10.0, None, 2, 164.0, id="retry-allowance-only"),
        ],
    )
    def test_interval_formula(
        self,
        interval: float,
        timeout: float | None,
        retry: int,
        expected: float,
    ) -> None:
        """The interval formula budgets deterministic maximum jitter."""
        reg = _reg(interval=interval, timeout=timeout, retry=retry)

        assert derive_stale_after(reg) == expected

    @pytest.mark.parametrize(
        ("backoff", "expected"),
        [
            pytest.param(None, 164.0, id="default-backoff-72"),
            pytest.param(FixedBackoff(delay=5.0), 140.0, id="short-cap-keeps-floor"),
            pytest.param(FixedBackoff(delay=50.0), 140.0, id="cap-at-floor"),
            pytest.param(
                ExponentialBackoff(max_delay=300.0), 740.0, id="long-exponential-cap"
            ),
            pytest.param(LinearBackoff(max_delay=90.0), 236.0, id="long-linear-cap"),
            pytest.param(FixedBackoff(delay=120.0), 308.0, id="long-fixed-delay"),
        ],
    )
    def test_retry_allowance_follows_backoff_cap(
        self, backoff: object, expected: float
    ) -> None:
        """Each retry allows the backoff's ``max_delay``, never below 60 s.

        Technique: Boundary Value Analysis — cap below, at and above 60 s.
        """
        # Arrange
        reg = _reg(interval=10.0, timeout=None, retry=2, backoff=backoff)

        # Act
        result = derive_stale_after(reg)

        # Assert
        assert result == expected

    @pytest.mark.parametrize(
        "cap",
        [
            pytest.param(None, id="no-attribute-value"),
            pytest.param(True, id="bool"),
            pytest.param("300", id="non-numeric"),
            pytest.param(float("inf"), id="infinite"),
            pytest.param(float("nan"), id="nan"),
            pytest.param(float("-inf"), id="negative-infinite"),
            pytest.param(10**1000, id="overflowing-integer"),
            pytest.param(-(10**1000), id="negative-overflowing-integer"),
            pytest.param(-1, id="negative"),
        ],
    )
    def test_custom_backoff_without_usable_cap_uses_floor(self, cap: object) -> None:
        """A custom strategy whose ``max_delay`` is unusable gets 60 s per retry.

        Technique: Error Guessing — duck-typed attribute of the wrong shape.
        """

        # Arrange
        class _Custom:
            max_delay = cap

            def delay(self, attempt: int) -> float:  # noqa: ARG002
                return 1.0

        reg = _reg(interval=10.0, timeout=None, retry=2, backoff=_Custom())

        # Act
        result = derive_stale_after(reg)

        # Assert
        assert result == 140.0

    def test_custom_backoff_cap_is_honoured(self) -> None:
        """A custom strategy exposing a numeric ``max_delay`` sets the allowance.

        Technique: Specification-based Testing — the documented duck-typed hook.
        """

        # Arrange
        class _Custom:
            max_delay = 600

            def delay(self, attempt: int) -> float:  # noqa: ARG002
                return 600.0

        reg = _reg(interval=10.0, timeout=None, retry=1, backoff=_Custom())

        # Act
        result = derive_stale_after(reg)

        # Assert
        assert result == 620.0

    def test_cron_uses_the_regular_period(self) -> None:
        """A five-minute cron schedule derives two periods: 600 s."""
        reg = _reg(interval=None, schedule=CronSchedule("0 0/5 * * * ?"))

        assert derive_stale_after(reg) == 600.0

    @pytest.mark.parametrize(
        "reg",
        [
            _reg(interval=1e308),
            _reg(timeout=1e308, retry=2),
            _reg(backoff=FixedBackoff(1e308), retry=2),
            _reg(retry=10**1000),
        ],
        ids=["period", "timeout", "retry-sleeps", "retry-count"],
    )
    def test_derived_overflow_raises_actionable_value_error(
        self, reg: _TelemetryRegistration
    ) -> None:
        """Overflow must not create an infinite, ineffective watchdog.

        Technique: Boundary Value Analysis — finite inputs overflow arithmetic.
        """
        with pytest.raises(ValueError, match="Derived stale_after.*finite.*explicitly"):
            derive_stale_after(reg)

    def test_cron_uses_the_longest_gap(self) -> None:
        """An irregular schedule (08:00, 10:00) is bounded by its 22 h gap."""
        reg = _reg(interval=None, schedule=CronSchedule("0 0 8,10 * * ?"))

        assert derive_stale_after(reg) == 2 * 22 * 3600.0

    def test_finite_cron_schedule_uses_its_remaining_gap(self) -> None:
        """A valid single-year schedule does not fail default derivation."""
        year = datetime.now(UTC).year + 1
        reg = _reg(
            interval=None,
            schedule=CronSchedule(f"0 0 0 1 1 ? {year}"),
        )

        assert derive_stale_after(reg) > 0


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

        # 2*10 + 4*(1+1) + 72*1
        assert app._telemetry[0].stale_after == 100.0  # noqa: SLF001


class TestStaleAfterPerDevice:
    """A callable ``stale_after`` under name expansion, like ``timeout=``.

    Technique: Equivalence Partitioning — dict-name callables receive each
    device's config; list-name callables and omitted values fall through to
    the settings-level resolution.
    """

    @staticmethod
    def _resolve(app: App) -> dict[str, object]:
        settings = make_settings()
        _expand_telemetry_names(app._telemetry, settings)  # noqa: SLF001
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001
        resolve_stale_after(app._telemetry, settings)  # noqa: SLF001
        return {r.name: r.stale_after for r in app._telemetry}  # noqa: SLF001

    def test_dict_name_callable_receives_device_config(self) -> None:
        """Each expanded device resolves its own bound from its config."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"fast": 30.0, "slow": 3600.0},
            interval=10,
            stale_after=lambda bound: bound,
        )
        async def handler() -> dict[str, object]:
            return {}

        # Act
        bounds = self._resolve(app)

        # Assert
        assert bounds == {"fast": 30.0, "slow": 3600.0}

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), True, "60"])
    def test_invalid_per_device_result_raises(self, bad: object) -> None:
        """A per-device bound that is not a finite positive number fails."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"bad": bad},
            interval=10,
            stale_after=lambda cfg: cfg,
        )
        async def handler() -> dict[str, object]:
            return {}

        # Act / Assert
        with pytest.raises(ValueError, match="Per-device stale_after for 'bad'"):
            _expand_telemetry_names(app._telemetry, make_settings())  # noqa: SLF001

    def test_list_name_callable_receives_settings(self) -> None:
        """Without per-device config the callable is resolved with Settings."""
        # Arrange
        app = App(name="testapp", version="1.0.0")
        seen: list[object] = []

        def bound(arg: object) -> float:
            seen.append(arg)
            return 45.0

        @app.telemetry(name=lambda s: ["a", "b"], interval=10, stale_after=bound)
        async def handler() -> dict[str, object]:
            return {}

        # Act
        bounds = self._resolve(app)

        # Assert
        assert bounds == {"a": 45.0, "b": 45.0}
        assert len(seen) == 2
        assert all(isinstance(arg, Settings) for arg in seen)

    def test_omitted_derives_from_each_device_interval(self) -> None:
        """An omitted bound derives per device from its resolved interval."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"a": 5.0, "b": 50.0},
            interval=lambda period: period,
            timeout=None,
        )
        async def handler() -> dict[str, object]:
            return {}

        # Act
        bounds = self._resolve(app)

        # Assert — 2 × interval with no timeout and no retry
        assert bounds == {"a": 10.0, "b": 100.0}


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

    async def test_stale_at_exactly_stale_after(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """An age equal to the bound is stale, without a watchdog delay."""
        reporter.track_freshness("sensor", 100.0)
        clock.advance(100.0)

        await reporter.check_freshness()

        assert _payloads(mock_mqtt, SENSOR_AVAILABILITY) == ["offline"]
        assert reporter.is_unavailable("sensor", source="freshness")

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

    async def test_stale_offline_is_reannounced_after_broker_outage(
        self, reporter: HealthReporter, clock: FakeClock, mock_mqtt: MockMqttClient
    ) -> None:
        """Reconnect repairs an offline publish that failed at the transition."""
        reporter.track_freshness("sensor", 10.0)
        clock.advance(10.0)
        mock_mqtt.raise_on_publish = MqttNotConnectedError("down")

        await reporter.check_freshness()

        mock_mqtt.raise_on_publish = None
        await reporter.reannounce()

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
        failing_since = entry.pop("failing_since")
        assert entry == {
            "status": "stale",
            "last_success_at": None,
            "consecutive_failures": 2,
            "last_error": "TransportError",
        }
        assert failing_since.endswith("+00:00")

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

    async def test_maximum_jitter_retry_cycle_stays_online(self) -> None:
        """A valid long retry cycle completes before derived freshness expires.

        Technique: Boundary Value Analysis — maximum positive jitter, three
        retries, and an interval small relative to the backoff cap.
        """
        calls = 0

        def factory() -> Any:
            async def sensor() -> dict[str, int]:
                nonlocal calls
                calls += 1
                if 2 <= calls <= 4:
                    raise TransportError("transient read failure")
                return {"value": calls}

            return sensor

        with patch("cosalette._retry.random.uniform", return_value=1.2):
            payloads = await _run_for(
                factory,
                seconds=1100,
                timeout=None,
                retry=3,
                retry_on=(TransportError,),
                backoff=FixedBackoff(300),
            )

        assert calls >= 5
        assert payloads == ["online"]

    async def test_slow_failure_expires_by_elapsed_time_and_recovers(self) -> None:
        """Freshness can expire after one failed cycle, before another finishes.

        Technique: State Transition Testing — online, elapsed-time stale,
        recovery; slow handler duration is additional to the interval.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)
        calls = 0
        completed_failures = 0

        @harness.app.telemetry(
            "sensor", interval=10, timeout=None, stale_after=35, unavailable_on=None
        )
        async def sensor() -> dict[str, int]:
            nonlocal calls, completed_failures
            calls += 1
            if calls in (2, 3):
                await clock.sleep(20)
                if calls == 2:
                    completed_failures += 1
                    raise TransportError("slow failed read")
            return {"value": calls}

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(AVAILABILITY, 1)
            await harness.wait_for_publish_count("testapp/sensor/state", 1)
            await clock.settle()
            for _ in range(7):
                await harness.advance_time(5)

            assert completed_failures == 1
            assert calls == 2
            assert _payloads(harness.mqtt, AVAILABILITY) == ["online", "offline"]

            # Freshness stays offline while the subsequent slow cycle runs.
            for _ in range(2):
                await harness.advance_time(5)
            assert calls == 3
            assert completed_failures == 1
            assert _payloads(harness.mqtt, AVAILABILITY) == ["online", "offline"]

            for _ in range(4):
                await harness.advance_time(5)
            await harness.wait_for_publish_count(AVAILABILITY, 3)
            assert calls == 3
            assert _payloads(harness.mqtt, AVAILABILITY) == [
                "online",
                "offline",
                "online",
            ]
        finally:
            harness.trigger_shutdown()
            await task

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

    @pytest.mark.parametrize(
        ("unavailable_on", "expected"),
        [
            pytest.param(None, ["online"], id="freshness-only-tolerates-one-cycle"),
            pytest.param(
                (TransportError,),
                ["online", "offline", "online"],
                id="failure-mark-fires-on-first-cycle",
            ),
        ],
    )
    async def test_unavailable_on_none_defers_offline_to_stale_after(
        self, unavailable_on: tuple[type[Exception], ...] | None, expected: list[str]
    ) -> None:
        """``unavailable_on=None`` + ``stale_after`` tolerates a lone failed cycle.

        Elapsed-time tolerance, rather than a consecutive-cycle
        ``unavailable_after=`` threshold (ADR-077 amendment, cos-4mv5.10).

        Technique: Decision Table Testing — failure mark on/off for one
        transient failed cycle inside the ``stale_after`` window.
        """
        # Arrange
        calls = 0

        def factory() -> Any:
            async def sensor() -> dict[str, int]:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise TransportError("one dropped BLE read")
                return {"value": calls}

            return sensor

        # Act
        payloads = await _run_for(
            factory,
            seconds=60,
            stale_after=35.0,
            unavailable_on=unavailable_on,
        )

        # Assert
        assert payloads == expected
