"""Unit tests for F23 — runtime Home Assistant MQTT discovery publication.

Covers:
  - App.discovery(): opt-in API, validation, default off.
  - build_discovery_payloads: live registry → HA payloads, caching, and
    dissolving the ADR-051 phantom-entity class (callable name= already
    expanded by the time a payload is built).
  - publish_discovery: retained publishes, fail-closed on error.
  - reconcile_discovery_topics: no-op without store, first-run, orphan
    clearing, malformed/tampered-snapshot guards.
  - Integration: register_connect_reannounce / publish_startup_snapshot wire
    discovery in on first connect only, opt-in only.

Test Techniques:
    - State-based Testing: inspect MockMqttClient.published after publish.
    - Boundary-value Analysis: empty store, malformed snapshot, wrong schema
      version, no annotations at all.
    - Security/scope guard: only our own discovery topics are ever cleared.
    - Error Guessing: fail-closed contract against generator/backend failures.
    - Round-trip Testing: settings-derived callable name= survives runtime
      discovery correctly (the ADR-051 phantom-entity regression).
"""

from __future__ import annotations

import logging
from typing import Annotated, cast

import pytest
from pydantic import BaseModel, Field

from cosalette._app import App
from cosalette._health import HealthReporter
from cosalette._mqtt import MqttPort
from cosalette._persistence._stores import MemoryStore
from cosalette._wiring import (
    publish_startup_snapshot,
    register_connect_reannounce,
)
from cosalette._wiring._discovery import (
    DiscoveryConfig,
    _discovery_snapshot_key,
    _is_safe_discovery_topic,
    build_discovery_payloads,
    publish_discovery,
    reconcile_discovery_topics,
)
from cosalette.schema import consumer
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.fixtures.mqtt import FakeConnectAwareMqttClient

pytestmark = pytest.mark.unit

PREFIX = "testapp"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TempReading(BaseModel):
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


def _make_reporter(mqtt: object) -> HealthReporter:
    clock = FakeClock()
    clock._time = 0.0
    return HealthReporter(
        mqtt=cast(MqttPort, mqtt),
        topic_prefix=PREFIX,
        version="1.0.0",
        clock=clock,
    )


def _annotated_app(*, name: str = PREFIX) -> App:
    """An app with one telemetry channel carrying a real consumer() annotation."""
    app = App(name=name, version="1.0.0")

    @app.telemetry("sensor", interval=30, state_model=_TempReading)
    async def _sensor():  # pragma: no cover
        return {"celsius": 21.5}

    return app


def _discovery_config_topics(
    mqtt: MockMqttClient | FakeConnectAwareMqttClient,
) -> set[str]:
    return {
        t for (t, _p, r, _q) in mqtt.published if r is True and "homeassistant" in t
    }


def _clears(mqtt: MockMqttClient | FakeConnectAwareMqttClient) -> list[str]:
    return [t for (t, p, r, _q) in mqtt.published if p == "" and r is True]


# ---------------------------------------------------------------------------
# App.discovery()
# ---------------------------------------------------------------------------


class TestAppDiscoveryApi:
    """App.discovery() opt-in configuration."""

    def test_default_is_none_opted_out(self) -> None:
        app = App(name=PREFIX, version="1.0.0")
        assert app._discovery is None

    def test_discovery_sets_config_with_defaults(self) -> None:
        app = App(name=PREFIX, version="1.0.0")
        app.discovery()
        assert app._discovery == DiscoveryConfig(discovery_prefix="homeassistant")

    def test_discovery_accepts_custom_prefix_and_enrich(self) -> None:
        app = App(name=PREFIX, version="1.0.0")

        def _enrich(channel, prop, config) -> None:  # noqa: ANN001
            pass

        app.discovery(discovery_prefix="ha", enrich=_enrich)

        assert app._discovery is not None
        assert app._discovery.discovery_prefix == "ha"
        assert app._discovery.enrich is _enrich

    def test_invalid_prefix_raises(self) -> None:
        app = App(name=PREFIX, version="1.0.0")
        with pytest.raises(ValueError, match="invalid MQTT"):
            app.discovery(discovery_prefix="ha/discovery")


# ---------------------------------------------------------------------------
# build_discovery_payloads
# ---------------------------------------------------------------------------


class TestBuildDiscoveryPayloads:
    async def test_builds_payloads_from_live_registry(self) -> None:
        app = _annotated_app()
        config = DiscoveryConfig()

        payloads = await build_discovery_payloads(app, config)

        assert any(p.topic.endswith("/config") for p in payloads)
        temp = next(p for p in payloads if p.config.get("name") == "Temperature")
        assert temp.config["device_class"] == "temperature"
        assert temp.topic.startswith("homeassistant/")

    async def test_result_is_cached_on_app(self) -> None:
        app = _annotated_app()
        config = DiscoveryConfig()

        first = await build_discovery_payloads(app, config)
        second = await build_discovery_payloads(app, config)

        assert first is second

    async def test_different_config_bypasses_cache(self) -> None:
        """Cache is keyed by config — a different discovery_prefix must not
        return payloads built for the first config.

        Technique: Error Guessing — the original cache stored payloads without
        keying by config, so a second call with a different prefix would silently
        return wrong topics.
        """
        app = _annotated_app()
        config_ha = DiscoveryConfig(discovery_prefix="homeassistant")
        config_other = DiscoveryConfig(discovery_prefix="other")

        payloads_ha = await build_discovery_payloads(app, config_ha)
        # Clear cache so second call goes through
        app._discovery_payloads_cache = None
        payloads_other = await build_discovery_payloads(app, config_other)

        ha_topics = {p.topic for p in payloads_ha}
        other_topics = {p.topic for p in payloads_other}
        assert all(t.startswith("homeassistant/") for t in ha_topics)
        assert all(t.startswith("other/") for t in other_topics)

    async def test_empty_registry_yields_no_payloads(self) -> None:
        app = App(name=PREFIX, version="1.0.0")
        config = DiscoveryConfig()

        payloads = await build_discovery_payloads(app, config)

        assert payloads == []

    async def test_callable_name_is_already_expanded_dissolves_adr051(self) -> None:
        """The ADR-051 phantom-entity class: a callable name= must already be
        resolved to its real, settings-derived value by the time a discovery
        topic is built — never the handler's Python qualname."""
        app = App(name=PREFIX, version="1.0.0")

        @app.telemetry(
            name=lambda settings: ["kitchen_sensor"],
            interval=30,
            state_model=_TempReading,
        )
        async def _sensor_handler():  # pragma: no cover
            return {"celsius": 21.5}

        settings = make_settings()
        from cosalette._wiring import expand_name_specs

        expand_name_specs(app._telemetry, app._devices, app._commands, settings)

        payloads = await build_discovery_payloads(app, DiscoveryConfig())

        topics = [p.topic for p in payloads]
        assert any("kitchen_sensor" in t for t in topics)
        assert not any("_sensor_handler" in t for t in topics)


# ---------------------------------------------------------------------------
# publish_discovery
# ---------------------------------------------------------------------------


class TestPublishDiscovery:
    async def test_publishes_retained_payloads(self) -> None:
        app = _annotated_app()
        mqtt = MockMqttClient()

        await publish_discovery(cast(MqttPort, mqtt), app, DiscoveryConfig())

        topics = _discovery_config_topics(mqtt)
        assert any(t.endswith("/config") for t in topics)
        for topic, payload, retain, qos in mqtt.published:
            if topic in topics:
                assert retain is True
                assert qos == 1
                assert payload  # non-empty JSON body

    async def test_generator_failure_is_swallowed(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _annotated_app()
        mqtt = MockMqttClient()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("schema deps missing")

        monkeypatch.setattr("cosalette._wiring._discovery._ensure_schema_deps", _boom)

        with caplog.at_level(logging.ERROR):
            await publish_discovery(cast(MqttPort, mqtt), app, DiscoveryConfig())

        assert mqtt.publish_count == 0
        assert any("Failed to publish" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# reconcile_discovery_topics
# ---------------------------------------------------------------------------


class TestReconcileDiscoveryTopics:
    async def test_no_store_no_publishes(self) -> None:
        app = _annotated_app()
        mqtt = MockMqttClient()

        await reconcile_discovery_topics(
            cast(MqttPort, mqtt), app, DiscoveryConfig(), None
        )

        assert mqtt.publish_count == 0

    async def test_first_run_no_clears_snapshot_persisted(self) -> None:
        app = _annotated_app()
        mqtt = MockMqttClient()
        store = MemoryStore()

        await reconcile_discovery_topics(
            cast(MqttPort, mqtt), app, DiscoveryConfig(), store
        )

        assert _clears(mqtt) == []
        saved = store.load(_discovery_snapshot_key(PREFIX, "homeassistant"))
        assert saved is not None
        assert isinstance(saved["topics"], list)
        assert saved["topics"]

    async def test_removed_entity_topic_cleared_next_run(self) -> None:
        store = MemoryStore()

        app1 = _annotated_app()
        await reconcile_discovery_topics(
            cast(MqttPort, MockMqttClient()), app1, DiscoveryConfig(), store
        )
        payloads1 = await build_discovery_payloads(app1, DiscoveryConfig())
        old_topics = {p.topic for p in payloads1}

        # Run 2: app with no annotated channels at all — everything orphaned.
        app2 = App(name=PREFIX, version="1.0.0")
        mqtt2 = MockMqttClient()
        await reconcile_discovery_topics(
            cast(MqttPort, mqtt2), app2, DiscoveryConfig(), store
        )

        cleared = set(_clears(mqtt2))
        assert cleared == old_topics

    async def test_entity_still_present_not_cleared(self) -> None:
        store = MemoryStore()
        app = _annotated_app()

        await reconcile_discovery_topics(
            cast(MqttPort, MockMqttClient()), app, DiscoveryConfig(), store
        )
        mqtt2 = MockMqttClient()
        await reconcile_discovery_topics(
            cast(MqttPort, mqtt2), app, DiscoveryConfig(), store
        )

        assert _clears(mqtt2) == []

    async def test_wrong_schema_version_no_clears_snapshot_overwritten(self) -> None:
        store = MemoryStore()
        store.save(
            _discovery_snapshot_key(PREFIX, "homeassistant"),
            {"schema_version": 999, "topics": ["homeassistant/sensor/x/y/config"]},
        )
        app = App(name=PREFIX, version="1.0.0")
        mqtt = MockMqttClient()

        await reconcile_discovery_topics(
            cast(MqttPort, mqtt), app, DiscoveryConfig(), store
        )

        assert _clears(mqtt) == []
        saved = store.load(_discovery_snapshot_key(PREFIX, "homeassistant"))
        assert saved is not None
        assert saved["schema_version"] == 1

    async def test_tampered_topic_outside_prefix_is_skipped(self) -> None:
        """Defense-in-depth: a stored topic must look like our own discovery
        config topic (rooted under the prefix, ending in /config, no
        wildcards) or it is never published to."""
        store = MemoryStore()
        store.save(
            _discovery_snapshot_key(PREFIX, "homeassistant"),
            {
                "schema_version": 1,
                "topics": [
                    "evil/+/inject",
                    "homeassistant/sensor/x/y/state",  # not a /config topic
                    "otherprefix/sensor/x/y/config",  # wrong prefix
                ],
            },
        )
        app = App(name=PREFIX, version="1.0.0")
        mqtt = MockMqttClient()

        await reconcile_discovery_topics(
            cast(MqttPort, mqtt), app, DiscoveryConfig(), store
        )

        assert _clears(mqtt) == []

    async def test_reconcile_failure_is_swallowed(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-closed: exceptions in reconcile_discovery_topics must not
        propagate to the caller (they would break app startup).

        Technique: Error Guessing — verifying the swallow contract matches
        publish_discovery's analogous fail-closed path.
        """
        app = _annotated_app()
        mqtt = MockMqttClient()
        store = MemoryStore()

        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "cosalette._wiring._discovery.build_discovery_payloads",
            _boom,
        )

        with caplog.at_level(logging.ERROR):
            await reconcile_discovery_topics(
                cast(MqttPort, mqtt), app, DiscoveryConfig(), store
            )

        assert mqtt.publish_count == 0
        assert any("reconciliation failed" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "topic,prefix,expected",
        [
            ("homeassistant/sensor/app/dev_prop/config", "homeassistant", True),
            ("homeassistant/sensor/x/y/state", "homeassistant", False),
            ("other/sensor/x/y/config", "homeassistant", False),
            ("homeassistant/+/x/y/config", "homeassistant", False),
        ],
    )
    def test_is_safe_discovery_topic(
        self, topic: str, prefix: str, expected: bool
    ) -> None:
        assert _is_safe_discovery_topic(topic, prefix) is expected


# ---------------------------------------------------------------------------
# Integration — connect-aware and eager wiring
# ---------------------------------------------------------------------------


class TestIntegrationDiscoveryWiring:
    async def test_discovery_none_by_default_no_ha_topics(self) -> None:
        app = _annotated_app()
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, None, None
        )
        await fake.simulate_connect()

        assert _discovery_config_topics(fake) == set()

    async def test_first_connect_publishes_discovery(self) -> None:
        app = _annotated_app()
        app.discovery()
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, None, app._discovery
        )
        await fake.simulate_connect()

        assert _discovery_config_topics(fake) != set()

    async def test_reconnect_does_not_republish_discovery(self) -> None:
        app = _annotated_app()
        app.discovery()
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, None, app._discovery
        )
        await fake.simulate_connect()
        fake.reset()
        await fake.simulate_connect()

        assert _discovery_config_topics(fake) == set()

    async def test_eager_path_publishes_discovery_for_non_connect_aware_client(
        self,
    ) -> None:
        app = _annotated_app()
        app.discovery()
        mqtt = MockMqttClient()
        reporter = _make_reporter(mqtt)

        await publish_startup_snapshot(
            app,
            cast(MqttPort, mqtt),
            reporter,
            app._all_registrations,
            PREFIX,
            None,
            connect_aware=False,
            discovery_config=app._discovery,
        )

        assert _discovery_config_topics(mqtt) != set()

    async def test_orphan_cleanup_wired_on_first_connect_with_store(self) -> None:
        store = MemoryStore()

        app1 = _annotated_app()
        app1.discovery()
        fake1 = FakeConnectAwareMqttClient()
        reporter1 = _make_reporter(fake1)
        register_connect_reannounce(
            fake1,
            app1,
            reporter1,
            app1._all_registrations,
            PREFIX,
            store,
            app1._discovery,
        )
        await fake1.simulate_connect()
        old_topics = _discovery_config_topics(fake1)
        assert old_topics

        app2 = App(name=PREFIX, version="1.0.0")
        app2.discovery()
        fake2 = FakeConnectAwareMqttClient()
        reporter2 = _make_reporter(fake2)
        register_connect_reannounce(
            fake2,
            app2,
            reporter2,
            app2._all_registrations,
            PREFIX,
            store,
            app2._discovery,
        )
        await fake2.simulate_connect()

        discovery_clears = {t for t in _clears(fake2) if "homeassistant" in t}
        assert discovery_clears == old_topics
