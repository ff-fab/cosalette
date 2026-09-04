"""Unit tests for the ADR-069 ``{prefix}/_meta/state_model_drift`` topic.

ADR-068 clause F warns once per registration when ``state_model=M`` and a
``-> N`` annotation disagree.  ADR-069 publishes the same fact as a retained
JSON snapshot so a fleet can be scraped with one subscription.  These tests pin
the wire contract (topic, flags, payload schema), the reconnect behaviour, and
the four sites the topic has to appear in to stay usable — the publish path,
the schema-validation skip list, both ACL principals, and the ``ai help``
companion text.

Test Techniques Used:
    - Specification-based Testing: clauses A-C and G — topic name, retained /
      QoS 1 / always-on flags, payload envelope and record fields.
    - Equivalence Partitioning: handler classes that do and do not drift
      (loose ``dict`` annotation / different model / same model / no
      annotation), and archetypes in and out of clause F scope (telemetry and
      command vs device).
    - Boundary Value Analysis: the empty drift set — ``drift_count: 0`` is
      published rather than suppressed, the boundary between "clean" and
      "never ran a version that publishes drift".
    - State Transition Testing: first connect vs reconnect — the cached
      serialised payload must make the republish byte-identical.
    - Error Guessing: a broker failure must stay fire-and-forget, matching
      ``publish_registry_snapshot``.
"""

from __future__ import annotations

import logging
import warnings
from typing import Annotated, Any, cast

import pytest
from pydantic import BaseModel, Field

from cosalette._app import App
from cosalette._constants import STATE_MODEL_DRIFT_TOPIC_SUFFIX
from cosalette._context import DeviceContext
from cosalette._health import HealthReporter
from cosalette._json import loads as _json_loads
from cosalette._mqtt import MqttPort
from cosalette._schema import (
    EnforcementConfig,
    SchemaRegistry,
)
from cosalette._schema._acl import derive_acl_principals
from cosalette._schema._validator import build_skip_topics
from cosalette._wiring import (
    publish_startup_snapshot,
    publish_state_model_drift_snapshot,
    register_connect_reannounce,
)
from cosalette._wiring._discovery import DiscoveryConfig, build_discovery_payloads
from cosalette.schema import consumer
from cosalette.testing import FakeClock, MockMqttClient
from tests.fixtures.mqtt import FakeConnectAwareMqttClient

pytestmark = pytest.mark.unit

PREFIX = "testapp"
DRIFT_TOPIC = f"{PREFIX}/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}"


class Reading(BaseModel):
    """The declared contract under test.

    The ``consumer()`` annotation is what makes the model discoverable, so the
    "no discovery entity for the drift topic" test has real entities to
    contrast against.
    """

    celsius: Annotated[
        float,
        Field(
            json_schema_extra=consumer(
                display_name="Temperature",
                device_class="temperature",
                unit="°C",
                state_class="measurement",
            )
        ),
    ]


class Other(BaseModel):
    """A second, incompatible contract."""

    value: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_silently(build: Any) -> App:
    """Run *build* with the clause F warning recorded instead of raised.

    The suite runs with ``filterwarnings = ["error"]``, so registering a
    deliberately drifting handler would fail the test rather than exercise it.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return build()


def _drifting_app() -> App:
    """An app mixing drifting and non-drifting registrations.

    Partitions covered: telemetry with a loose ``dict`` annotation (drifts),
    command annotated with a different model (drifts), telemetry annotated with
    the declared model (silent), telemetry with no annotation (silent), and a
    device with ``state_model=`` (out of clause F scope entirely).
    """

    def build() -> App:
        app = App(name=PREFIX, version="1.0.0")

        @app.telemetry("brightness", interval=30, state_model=Reading)
        async def _brightness() -> dict[str, object]:  # pragma: no cover
            return {}

        @app.command("mode", state_model=Reading)
        async def _mode() -> Other:  # pragma: no cover
            return Other(value=1)

        @app.telemetry("climate", interval=30, state_model=Reading)
        async def _climate() -> Reading:  # pragma: no cover
            return Reading(celsius=21.5)

        @app.telemetry("humidity", interval=30, state_model=Reading)
        async def _humidity():  # pragma: no cover
            return {}

        @app.device("valve", state_model=Other)
        async def _valve(ctx: DeviceContext):  # pragma: no cover
            yield

        return app

    return _register_silently(build)


def _clean_app() -> App:
    """An app whose declarations all agree — no drift to report."""
    app = App(name=PREFIX, version="1.0.0")

    @app.telemetry("climate", interval=30, state_model=Reading)
    async def _climate() -> Reading:  # pragma: no cover
        return Reading(celsius=21.5)

    return app


def _make_reporter(mqtt: object) -> HealthReporter:
    clock = FakeClock()
    clock._time = 0.0
    return HealthReporter(
        mqtt=cast(MqttPort, mqtt),
        topic_prefix=PREFIX,
        version="1.0.0",
        clock=clock,
    )


def _drift_publishes(
    mqtt: MockMqttClient | FakeConnectAwareMqttClient,
) -> list[tuple[str, str, bool, int]]:
    return [rec for rec in mqtt.published if rec[0] == DRIFT_TOPIC]


async def _publish(app: App) -> tuple[MockMqttClient, dict[str, Any]]:
    """Publish *app*'s snapshot and return the client plus the parsed payload."""
    mqtt = MockMqttClient()
    await publish_state_model_drift_snapshot(app, mqtt, PREFIX)
    topic, payload, _retain, _qos = _drift_publishes(mqtt)[0]
    assert topic == DRIFT_TOPIC
    return mqtt, cast(dict[str, Any], _json_loads(payload))


# ---------------------------------------------------------------------------
# Payload contract (clauses A-C)
# ---------------------------------------------------------------------------


class TestDriftSnapshotPayload:
    """The published document lists exactly the drifting handlers."""

    async def test_drifting_telemetry_is_reported_with_both_labels(self) -> None:
        # Arrange
        app = _drifting_app()

        # Act
        _mqtt, payload = await _publish(app)

        # Assert
        entry = next(e for e in payload["entries"] if e["handler"] == "brightness")
        assert entry == {
            "handler": "brightness",
            "archetype": "telemetry",
            "kind": "annotation_conflict",
            "declared_model": "Reading",
            "effective_annotation": "dict",
        }

    async def test_drifting_command_is_reported_with_command_archetype(self) -> None:
        # Arrange
        app = _drifting_app()

        # Act
        _mqtt, payload = await _publish(app)

        # Assert
        entry = next(e for e in payload["entries"] if e["handler"] == "mode")
        assert entry["archetype"] == "command"
        assert entry["declared_model"] == "Reading"
        assert entry["effective_annotation"] == "Other"

    async def test_envelope_carries_schema_version_and_drift_count(self) -> None:
        # Arrange
        app = _drifting_app()

        # Act
        _mqtt, payload = await _publish(app)

        # Assert
        assert payload["schema_version"] == 1
        assert payload["drift_count"] == 2
        assert payload["drift_count"] == len(payload["entries"])

    async def test_agreeing_and_unannotated_handlers_are_absent(self) -> None:
        """Only contradictions are reported.

        Technique: Equivalence Partitioning — ``-> Reading`` and an unannotated
        handler are separate silent partitions of clause F.
        """
        # Arrange
        app = _drifting_app()

        # Act
        _mqtt, payload = await _publish(app)

        # Assert
        reported = {e["handler"] for e in payload["entries"]}
        assert reported == {"brightness", "mode"}

    async def test_device_archetype_is_out_of_scope(self) -> None:
        """``@app.device`` has no return contract, so it can never drift."""
        # Arrange
        app = _drifting_app()

        # Act
        _mqtt, payload = await _publish(app)

        # Assert
        archetypes = {e["archetype"] for e in payload["entries"]}
        assert archetypes <= {"telemetry", "command"}
        assert "valve" not in {e["handler"] for e in payload["entries"]}

    async def test_clean_app_publishes_zero_drift_snapshot(self) -> None:
        """A clean app publishes ``drift_count: 0`` rather than nothing.

        Technique: Boundary Value Analysis — the empty drift set is what
        separates "healthy" from "never ran a version that publishes drift".
        """
        # Arrange
        app = _clean_app()

        # Act
        _mqtt, payload = await _publish(app)

        # Assert
        assert payload == {"schema_version": 1, "drift_count": 0, "entries": []}

    async def test_payload_omits_app_timestamp_and_version_fields(self) -> None:
        """Clause C's deliberate absences — the topic prefix carries the app,
        and a timestamp would break reconnect byte-identity.
        """
        # Arrange
        app = _drifting_app()

        # Act
        _mqtt, payload = await _publish(app)

        # Assert
        assert set(payload) == {"schema_version", "drift_count", "entries"}
        assert all(
            set(entry)
            == {
                "handler",
                "archetype",
                "kind",
                "declared_model",
                "effective_annotation",
            }
            for entry in payload["entries"]
        )


# ---------------------------------------------------------------------------
# Publication behaviour (clauses B and D)
# ---------------------------------------------------------------------------


class TestDriftSnapshotPublication:
    """Retained, QoS 1, republished on every connect, fire-and-forget."""

    async def test_publishes_retained_at_qos_1(self) -> None:
        # Arrange
        app = _drifting_app()
        mqtt = MockMqttClient()

        # Act
        await publish_state_model_drift_snapshot(app, mqtt, PREFIX)

        # Assert
        topic, payload, retain, qos = _drift_publishes(mqtt)[0]
        assert topic == DRIFT_TOPIC
        assert retain is True
        assert qos == 1
        assert isinstance(payload, str)

    async def test_republish_is_byte_identical(self) -> None:
        """The cached serialisation makes a reconnect republish identical.

        Technique: State Transition Testing — first publish vs republish.
        """
        # Arrange
        app = _drifting_app()
        mqtt = MockMqttClient()

        # Act
        await publish_state_model_drift_snapshot(app, mqtt, PREFIX)
        await publish_state_model_drift_snapshot(app, mqtt, PREFIX)

        # Assert
        first, second = _drift_publishes(mqtt)
        assert first[1] == second[1]

    async def test_publish_failure_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Technique: Error Guessing — a dead broker must not abort startup."""
        # Arrange
        app = _drifting_app()
        mqtt = MockMqttClient(raise_on_publish=RuntimeError("broker down"))

        # Act
        with caplog.at_level(logging.ERROR, logger="cosalette._wiring"):
            await publish_state_model_drift_snapshot(app, mqtt, PREFIX)

        # Assert
        assert "Failed to publish state_model drift snapshot" in caplog.text

    async def test_connect_callback_publishes_on_every_connect(self) -> None:
        """Retained messages die with the broker, so every connect republishes."""
        # Arrange
        app = _drifting_app()
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)
        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, None, None
        )

        # Act
        await fake.simulate_connect()
        await fake.simulate_connect()

        # Assert
        assert len(_drift_publishes(fake)) == 2

    async def test_startup_snapshot_publishes_for_non_connect_aware_client(
        self,
    ) -> None:
        # Arrange
        app = _drifting_app()
        mqtt = MockMqttClient()
        reporter = _make_reporter(mqtt)

        # Act
        await publish_startup_snapshot(
            app,
            mqtt,
            reporter,
            app._all_registrations,
            PREFIX,
            None,
            connect_aware=False,
        )

        # Assert
        assert len(_drift_publishes(mqtt)) == 1

    async def test_startup_snapshot_skipped_for_connect_aware_client(self) -> None:
        """Connect-aware adapters publish from the callback instead."""
        # Arrange
        app = _drifting_app()
        mqtt = MockMqttClient()
        reporter = _make_reporter(mqtt)

        # Act
        await publish_startup_snapshot(
            app,
            mqtt,
            reporter,
            app._all_registrations,
            PREFIX,
            None,
            connect_aware=True,
        )

        # Assert
        assert _drift_publishes(mqtt) == []


# ---------------------------------------------------------------------------
# Framework-owned topic (clause G) — the four sites that must stay in step
# ---------------------------------------------------------------------------


class TestFrameworkOwnedTopic:
    """The topic is framework-owned: no channel, no entity, ACL'd, skipped."""

    def test_topic_is_not_an_asyncapi_channel(self) -> None:
        # Arrange
        app = _drifting_app()

        # Act
        doc = app.asyncapi()

        # Assert
        addresses = {ch.get("address") for ch in doc.get("channels", {}).values()}
        assert DRIFT_TOPIC not in addresses

    async def test_topic_produces_no_discovery_entity(self) -> None:
        # Arrange
        app = _drifting_app()

        # Act
        payloads = await build_discovery_payloads(app, DiscoveryConfig())

        # Assert — entities are built (so the check is not vacuous), none of
        # them from the drift topic.
        assert payloads
        assert all(
            STATE_MODEL_DRIFT_TOPIC_SUFFIX not in p.topic
            and STATE_MODEL_DRIFT_TOPIC_SUFFIX not in str(p.config)
            for p in payloads
        )

    def test_topic_is_skipped_by_schema_validation(self) -> None:
        # Act
        skip_topics = build_skip_topics(PREFIX, frozenset())

        # Assert
        assert DRIFT_TOPIC in skip_topics

    def test_app_principal_may_publish_the_topic(self) -> None:
        # Arrange
        registry = _empty_registry()

        # Act
        principals = derive_acl_principals(registry, app_prefix=PREFIX)

        # Assert
        app_principal = next(p for p in principals if p.name == PREFIX)
        assert DRIFT_TOPIC in app_principal.publish_topics

    def test_monitor_principal_subscribes_the_fleet_wildcard(self) -> None:
        # Arrange
        registry = _empty_registry()

        # Act
        principals = derive_acl_principals(registry, app_prefix=PREFIX)

        # Assert
        monitor = next(p for p in principals if p.name == "monitor")
        assert f"+/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}" in monitor.subscribe_topics

    def test_ai_help_contracts_topic_documents_the_topic(self) -> None:
        """The companion surface is the fourth site the topic must appear in."""
        # Arrange
        from cosalette._ai_content._help import get_help_content

        # Act
        text = get_help_content("contracts")

        # Assert
        assert text is not None
        assert STATE_MODEL_DRIFT_TOPIC_SUFFIX in text


def _empty_registry() -> SchemaRegistry:
    """A registry with no channels — ACL framework topics do not need any."""
    return SchemaRegistry(
        app_name=PREFIX,
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="strict"),
        channels={},
        operations={},
        component_schemas={},
        device_names=frozenset(),
    )
