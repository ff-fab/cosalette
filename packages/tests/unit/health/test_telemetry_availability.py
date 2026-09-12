"""Unit tests for automatic per-device availability on telemetry failures.

ADR-077: the telemetry and device archetypes publish retained ``"offline"``
once a handler's retries are exhausted, and ``"online"`` on the next
successful poll, without the app opting in per handler.

Test Techniques Used:
    - Decision Table Testing: resolve_unavailable_on across spec x is_root
    - State Transition Testing: available -> unavailable -> recovered
    - Specification-based Testing: narrowing and disabling via unavailable_on
    - Mock-based Isolation: MockMqttClient records publishes, FakeClock for uptime
"""

from __future__ import annotations

import pytest

from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._registration import _UNSET, _TelemetryRegistration
from cosalette._registration._model import resolve_unavailable_on
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette.testing import FakeClock, MockMqttClient

pytestmark = pytest.mark.unit

PREFIX = "myapp"


class TransportError(Exception):
    """Stand-in for a downstream adapter error such as BleakError."""


@pytest.fixture
def mock_mqtt() -> MockMqttClient:
    return MockMqttClient()


@pytest.fixture
def reporter(mock_mqtt: MockMqttClient) -> HealthReporter:
    clock = FakeClock()
    clock._time = 0.0
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
    is_root: bool = False,
    unavailable_on: object = _UNSET,
) -> _TelemetryRegistration:
    return _TelemetryRegistration(
        name=name,
        func=_never_called,
        injection_plan=[],
        interval=60,
        is_root=is_root,
        unavailable_on=unavailable_on,  # ty: ignore[invalid-argument-type]
    )


def _payloads(mock_mqtt: MockMqttClient, topic: str) -> list[str]:
    return [payload for payload, *_ in mock_mqtt.get_messages_for(topic)]


# ---------------------------------------------------------------------------
# resolve_unavailable_on
# ---------------------------------------------------------------------------


class TestResolveUnavailableOn:
    """Decision table for the unavailable_on spec.

    Technique: Decision Table Testing — spec x is_root.
    """

    def test_unset_means_every_exception_for_a_named_entity(self) -> None:
        """A typed default is not constructible, so the default is blanket.

        The framework has no dependency on bleak/paramiko/pyserial and cannot
        name their exception types, so narrowing by default would silently
        fail to fire for exactly the adapters this exists for.
        """
        assert resolve_unavailable_on(_UNSET, is_root=False) == (Exception,)

    def test_unset_is_opt_in_for_a_root_entity(self) -> None:
        """Root publishes to the flat topic, so one read must not down the app."""
        assert resolve_unavailable_on(_UNSET, is_root=True) is None

    def test_none_disables(self) -> None:
        """None keeps the meaning it already has on @app.command."""
        assert resolve_unavailable_on(None, is_root=False) is None

    def test_explicit_tuple_narrows(self) -> None:
        """An explicit tuple is used verbatim."""
        assert resolve_unavailable_on((TransportError,), is_root=False) == (
            TransportError,
        )

    def test_explicit_tuple_opts_a_root_entity_in(self) -> None:
        """Root exclusion applies to the default only, not to an explicit tuple."""
        assert resolve_unavailable_on((TransportError,), is_root=True) == (
            TransportError,
        )


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


class TestAutomaticUnavailability:
    """Offline publishing from the telemetry failure path.

    Technique: State Transition Testing — the transition into unavailability.
    """

    async def test_failure_publishes_offline_with_no_opt_in(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """The reported defect: a failing device must stop claiming online."""
        await TelemetryRunner._handle_telemetry_error(
            _reg(),
            TransportError("no route to device"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == ["offline"]
        assert reporter.is_unavailable("sensor")

    async def test_offline_published_once_while_failure_persists(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Repeated failures must not republish an unchanged retained value."""
        reg = _reg()
        last: type[Exception] | None = None
        for _ in range(3):
            last = await TelemetryRunner._handle_telemetry_error(
                reg,
                TransportError("still down"),
                last,
                error_publisher,
                reporter,
                mark_unavailable=True,
            )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == ["offline"]

    async def test_status_blob_keeps_the_specific_reason(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Availability says offline; the status blob says why.

        Availability is a two-word vocabulary and must not become the
        diagnostic channel, so the reason stays in {prefix}/status.
        """
        await TelemetryRunner._handle_telemetry_error(
            _reg(),
            TransportError("boom"),
            None,
            error_publisher,
            reporter,
        )

        assert reporter._devices["sensor"].status == "error"

    async def test_narrowed_trigger_ignores_an_unlisted_exception(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """A handler bug need not claim the device is unreachable."""
        await TelemetryRunner._handle_telemetry_error(
            _reg(unavailable_on=(TransportError,)),
            ValueError("malformed payload"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == []
        assert not reporter.is_unavailable("sensor")

    async def test_narrowed_trigger_fires_for_a_listed_exception(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """The narrowed form still publishes for the types it names."""
        await TelemetryRunner._handle_telemetry_error(
            _reg(unavailable_on=(TransportError,)),
            TransportError("no route"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == ["offline"]

    async def test_none_disables_publishing(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """unavailable_on=None opts out entirely."""
        await TelemetryRunner._handle_telemetry_error(
            _reg(unavailable_on=None),
            TransportError("boom"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == []

    async def test_root_entity_not_downed_by_default(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """One failed root read must not declare the whole app unavailable."""
        await TelemetryRunner._handle_telemetry_error(
            _reg("app", is_root=True),
            TransportError("boom"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/availability") == []

    async def test_root_entity_participates_when_opted_in(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """An explicit tuple opts a root entity in, using the flat topic."""
        await TelemetryRunner._handle_telemetry_error(
            _reg("app", is_root=True, unavailable_on=(TransportError,)),
            TransportError("boom"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/availability") == ["offline"]

    async def test_non_exhausted_error_does_not_publish_offline(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Processing errors do not masquerade as exhausted handler retries.

        Technique: Branch Coverage - the generic error route leaves
        availability untouched unless the retry layer reports exhaustion.
        """
        await TelemetryRunner._handle_telemetry_error(
            _reg(),
            TransportError("normalization failed"),
            None,
            error_publisher,
            reporter,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == []


# ---------------------------------------------------------------------------
# Recovery path
# ---------------------------------------------------------------------------


class TestAutomaticRecovery:
    """Online republishing on the next successful poll.

    Technique: State Transition Testing — unavailable -> recovered.
    """

    async def test_successful_poll_republishes_online(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Telemetry auto-recovers, unlike ADR-047's command-only scoping."""
        reg = _reg()
        last = await TelemetryRunner._handle_telemetry_error(
            reg,
            TransportError("down"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )
        mock_mqtt.reset()

        await TelemetryRunner._clear_telemetry_error(
            reg.name, last, reporter, is_root=reg.is_root
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == ["online"]
        assert not reporter.is_unavailable("sensor")

    async def test_recovery_is_quiet_when_never_unavailable(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """A device that never went offline publishes no availability."""
        await TelemetryRunner._clear_telemetry_error("sensor", None, reporter)

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == []

    async def test_recovery_uses_the_flat_topic_for_a_root_entity(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Root recovery addresses {prefix}/availability, not {prefix}/app/..."""
        reg = _reg("app", is_root=True, unavailable_on=(TransportError,))
        last = await TelemetryRunner._handle_telemetry_error(
            reg,
            TransportError("down"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )
        mock_mqtt.reset()

        await TelemetryRunner._clear_telemetry_error(
            reg.name, last, reporter, is_root=reg.is_root
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/availability") == ["online"]

    async def test_offline_republished_after_a_recovery_cycle(
        self,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """A second outage publishes again — the transition latch resets."""
        reg = _reg()
        last = await TelemetryRunner._handle_telemetry_error(
            reg,
            TransportError("down"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )
        await TelemetryRunner._clear_telemetry_error(
            reg.name, last, reporter, is_root=reg.is_root
        )
        mock_mqtt.reset()

        await TelemetryRunner._handle_telemetry_error(
            reg,
            TransportError("down again"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        assert _payloads(mock_mqtt, f"{PREFIX}/sensor/availability") == ["offline"]


# ---------------------------------------------------------------------------
# Decorator plumbing
# ---------------------------------------------------------------------------


class TestUnavailableOnReachesRegistration:
    """The parameter must survive every entry point, not just the signature.

    Technique: Specification-based Testing — App and Router, decorator and
    imperative forms, telemetry and device archetypes.
    """

    def test_telemetry_decorator_default_is_unset(self) -> None:
        """Omitting the parameter leaves the sentinel for the runner to resolve."""
        from cosalette import App

        app = App("myapp")

        @app.telemetry("sensor", interval=60)
        async def read() -> dict[str, float]:
            return {"v": 1.0}  # pragma: no cover

        assert app._telemetry[0].unavailable_on is _UNSET

    def test_telemetry_decorator_forwards_explicit_tuple(self) -> None:
        """An explicit tuple reaches the registration verbatim."""
        from cosalette import App

        app = App("myapp")

        @app.telemetry("sensor", interval=60, unavailable_on=(TransportError,))
        async def read() -> dict[str, float]:
            return {"v": 1.0}  # pragma: no cover

        assert app._telemetry[0].unavailable_on == (TransportError,)

    def test_telemetry_decorator_forwards_none(self) -> None:
        """None reaches the registration and disables publishing."""
        from cosalette import App

        app = App("myapp")

        @app.telemetry("sensor", interval=60, unavailable_on=None)
        async def read() -> dict[str, float]:
            return {"v": 1.0}  # pragma: no cover

        assert app._telemetry[0].unavailable_on is None

    def test_device_decorator_forwards_explicit_tuple(self) -> None:
        """The device archetype threads the parameter too."""
        from collections.abc import AsyncIterator

        from cosalette import App

        app = App("myapp")

        @app.device("blind", unavailable_on=(TransportError,))
        async def run() -> AsyncIterator[None]:
            yield  # pragma: no cover

        assert app._devices[0].unavailable_on == (TransportError,)

    def test_router_telemetry_forwards_explicit_tuple(self) -> None:
        """Router parity: composition must not silently drop the parameter."""
        from cosalette import App, Router

        router = Router()

        @router.telemetry("sensor", interval=60, unavailable_on=(TransportError,))
        async def read() -> dict[str, float]:
            return {"v": 1.0}  # pragma: no cover

        app = App("myapp")
        app.include_router(router)

        assert app._telemetry[0].unavailable_on == (TransportError,)

    def test_router_device_forwards_explicit_tuple(self) -> None:
        """Router device path threads it through its positional forwarding."""
        from collections.abc import AsyncIterator

        from cosalette import App, Router

        router = Router()

        @router.device("blind", unavailable_on=(TransportError,))
        async def run() -> AsyncIterator[None]:
            yield  # pragma: no cover

        app = App("myapp")
        app.include_router(router)

        assert app._devices[0].unavailable_on == (TransportError,)

    @pytest.mark.parametrize(
        "unavailable_on",
        [TransportError, (BaseException,), (TransportError, "not-an-exception")],
    )
    def test_telemetry_rejects_invalid_unavailable_on(
        self, unavailable_on: object
    ) -> None:
        """Invalid availability specs fail while registering telemetry.

        Technique: Equivalence Partitioning - non-tuples, BaseException-only
        tuples, and mixed tuples are each outside the accepted input domain.
        """
        from cosalette import App

        app = App("myapp")

        async def read() -> dict[str, object]:
            return {"v": 1.0}

        with pytest.raises(TypeError, match="unavailable_on"):
            app.add_telemetry(
                "sensor",
                read,
                interval=60,
                unavailable_on=unavailable_on,  # ty: ignore[invalid-argument-type]
            )

    def test_device_rejects_invalid_unavailable_on(self) -> None:
        """Device registration applies the same Exception-subclass contract.

        Technique: Specification-based Testing - device and telemetry expose
        the same unavailable_on input contract.
        """
        from collections.abc import AsyncIterator

        from cosalette import App

        app = App("myapp")

        async def run() -> AsyncIterator[None]:
            yield

        with pytest.raises(TypeError, match="unavailable_on"):
            app.add_device(
                "sensor",
                run,
                unavailable_on=[TransportError],  # ty: ignore[invalid-argument-type]
            )
