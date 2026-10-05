"""Unit tests for the opt-in discovery instance identity (ADR-089).

Two instances of one app on a shared broker must not overwrite each other's
Home Assistant discovery configs or openHAB Things.  ``mqtt.instance_id``
replaces the app name in every identity field; unset, output stays
byte-identical to the pre-ADR-089 generator even under a custom topic prefix.

Test Techniques Used:
    - Regression (snapshot) Testing: unset instance id under a custom prefix
      matches output captured from the generator before ADR-089
    - Equivalence Partitioning: two distinct instance ids give disjoint
      identity sets (HA and openHAB)
    - Decision Table Testing: the startup warning condition
      (instance id x custom prefix x discovery enabled)
    - State-based Testing: orphan cleanup with a store shared by instances
    - Error Guessing: invalid instance ids, multi-app documents
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, cast
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field, ValidationError
from typer.testing import CliRunner

from cosalette._app import App
from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._mqtt import MqttPort
from cosalette._persistence._stores import MemoryStore
from cosalette._schema._cli import schema_app
from cosalette._schema._consumer_gen import (
    HaDiscoveryGenerator,
    HaDiscoveryPayload,
    OpenHabGenerator,
    ha_discovery_to_json,
)
from cosalette._schema._loader import load_schema
from cosalette._settings import MqttSettings, Settings
from cosalette._wiring._discovery import (
    DiscoveryConfig,
    build_discovery_payloads,
    reconcile_discovery_topics,
    resolve_discovery_config,
)
from cosalette.schema import consumer
from cosalette.testing import AppHarness, MockMqttClient

pytestmark = pytest.mark.unit

APP = "testapp"
PREFIX = "house/wiz"
FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "discovery"


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


def _app(*, with_sensor: bool = True) -> App:
    app = App(name=APP, version="1.0.0")
    if with_sensor:

        @app.telemetry("sensor", interval=30, state_model=_TempReading)
        async def _sensor():  # pragma: no cover
            return {"celsius": 21.5}

    return app


async def _payloads(
    instance_id: str | None, prefix: str = PREFIX
) -> list[HaDiscoveryPayload]:
    return await build_discovery_payloads(
        _app(), DiscoveryConfig(instance_id=instance_id), prefix
    )


async def _openhab(instance_id: str | None) -> OpenHabGenerator:
    registry = await load_schema(_app().asyncapi(topic_prefix=PREFIX))
    return OpenHabGenerator(registry=registry, instance_id=instance_id)


def _identities(payloads: list[HaDiscoveryPayload]) -> set[str]:
    """Every identity a payload set claims: topics, unique_ids, device ids."""
    ids: set[str] = set()
    for p in payloads:
        device = p.config["device"]
        ids |= {p.topic, p.config["unique_id"], *device["identifiers"]}
        if "via_device" in device:
            ids.add(device["via_device"])
    return ids


def _clears(mqtt: MockMqttClient) -> set[str]:
    return {t for (t, p, r, _q) in mqtt.published if p == "" and r is True}


class TestUnsetIsByteIdentical:
    """No instance id: output matches the pre-ADR-089 snapshot."""

    async def test_ha_discovery_matches_snapshot_under_custom_prefix(self) -> None:
        expected = (FIXTURES / "ha_custom_prefix_no_instance_id.json").read_text()

        assert ha_discovery_to_json(await _payloads(None)) + "\n" == expected

    async def test_openhab_matches_snapshot_under_custom_prefix(self) -> None:
        expected = (FIXTURES / "openhab_custom_prefix_no_instance_id.txt").read_text()
        generator = await _openhab(None)

        actual = generator.generate_things() + "// ---\n" + generator.generate_items()

        assert actual == expected


class TestDistinctInstances:
    """Two instance ids produce disjoint identities; transport is untouched."""

    async def test_ha_identities_are_disjoint(self) -> None:
        a, b = await _payloads("attic"), await _payloads("cellar")

        assert _identities(a).isdisjoint(_identities(b))

    async def test_ha_identities_use_instance_id(self) -> None:
        payloads = {p.config["unique_id"]: p for p in await _payloads("attic")}

        bridge = payloads["cosalette_attic_bridge"]
        sensor = payloads["cosalette_attic_sensor_celsius"]
        assert bridge.topic == "homeassistant/binary_sensor/attic/bridge/config"
        assert bridge.config["device"]["identifiers"] == ["cosalette_attic"]
        assert bridge.config["device"]["name"] == "attic"
        assert sensor.config["device"]["via_device"] == "cosalette_attic"
        assert sensor.config["origin"]["name"] == APP

    async def test_ha_topics_still_follow_topic_prefix(self) -> None:
        payloads = await _payloads("attic")

        state_topics = {p.config["state_topic"] for p in payloads}
        assert state_topics == {f"{PREFIX}/sensor/state", f"{PREFIX}/status"}

    async def test_openhab_identities_are_disjoint(self) -> None:
        a, b = await _openhab("attic"), await _openhab("cellar")

        things_a = a.generate_things() + a.generate_items()
        assert "mqtt:topic:broker:attic_sensor" in things_a
        assert "Attic_Sensor_Celsius" in things_a
        assert "(gAttic)" in things_a
        assert "testapp" not in things_a.replace(f"{PREFIX}/", "")
        assert "attic" not in b.generate_things() + b.generate_items()

    async def test_multi_app_document_rejected(self) -> None:
        registry = await load_schema(_app().asyncapi(topic_prefix=PREFIX))
        channel = next(iter(registry.channels.values()))
        other = dataclasses.replace(channel, app_name="otherapp")
        multi = dataclasses.replace(
            registry, channels={**registry.channels, "other": other}
        )

        with pytest.raises(ValueError, match="2 apps"):
            HaDiscoveryGenerator(registry=multi, instance_id="attic").generate()


@pytest.fixture
def _inline_store_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run store I/O inline (see test_app_discovery for the executor hang)."""

    async def _inline(
        func: Callable[..., object], /, *args: object, **kwargs: object
    ) -> object:
        return func(*args, **kwargs)

    monkeypatch.setattr("cosalette._wiring._discovery.to_thread", _inline)


@pytest.mark.usefixtures("_inline_store_io")
class TestOrphanCleanupIsPerInstance:
    async def test_instance_a_never_clears_instance_b(self) -> None:
        store = MemoryStore()
        attic, cellar = (
            DiscoveryConfig(instance_id="attic"),
            DiscoveryConfig(instance_id="cellar"),
        )
        for config in (attic, cellar):
            await reconcile_discovery_topics(
                cast(MqttPort, MockMqttClient()), _app(), config, store, PREFIX
            )
        attic_topics = {p.topic for p in await _payloads("attic")}

        # Instance A loses its entity: only A's topics are orphans.
        mqtt = MockMqttClient()
        await reconcile_discovery_topics(
            cast(MqttPort, mqtt), _app(with_sensor=False), attic, store, PREFIX
        )

        assert _clears(mqtt) == attic_topics
        assert all("/attic/" in t for t in _clears(mqtt))


class TestStartupWarning:
    """Warn exactly when a custom prefix meets enabled discovery, no instance id."""

    @pytest.mark.parametrize(
        ("discovery", "topic_prefix", "instance_id", "warns"),
        [
            (True, PREFIX, "", True),
            (True, PREFIX, "attic", False),
            (True, "", "", False),
            (True, APP, "", False),
            (False, PREFIX, "", False),
        ],
    )
    def test_warning_condition(
        self,
        caplog: pytest.LogCaptureFixture,
        discovery: bool,
        topic_prefix: str,
        instance_id: str,
        warns: bool,
    ) -> None:
        mqtt = MqttSettings(topic_prefix=topic_prefix, instance_id=instance_id)
        config = DiscoveryConfig() if discovery else None

        with caplog.at_level(logging.WARNING, logger="cosalette._wiring"):
            resolved = resolve_discovery_config(config, mqtt, APP)

        assert ("MQTT__INSTANCE_ID" in caplog.text) is warns
        if discovery:
            assert resolved is not None
            assert resolved.instance_id == (instance_id or None)
        else:
            assert resolved is None


class TestAppRun:
    """``App.run`` binds ``mqtt.instance_id`` into runtime discovery."""

    async def test_publishes_under_instance_id(self) -> None:
        harness = AppHarness.create(
            mqtt=MqttSettings(topic_prefix=PREFIX, instance_id="attic")
        )
        harness.app.discovery()

        @harness.app.telemetry("sensor", interval=30, state_model=_TempReading)
        async def _sensor():  # noqa: ANN202 — state_model= types the payload
            return {"celsius": 21.5}

        bridge = "homeassistant/binary_sensor/attic/bridge/config"
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count(bridge, 1)
        harness.trigger_shutdown()
        await task

        topics = {t for (t, *_r) in harness.mqtt.published}
        assert not any("/testapp/" in t for t in topics if "homeassistant" in t)


class TestInstanceIdSetting:
    def test_read_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MQTT__INSTANCE_ID", "attic")

        assert Settings(_env_file=None).mqtt.instance_id == "attic"

    @pytest.mark.parametrize("value", ["a/b", "a b", "a+b", "ä"])
    def test_rejects_unsafe_characters(self, value: str) -> None:
        with pytest.raises(ValidationError, match="instance_id"):
            MqttSettings(instance_id=value)


class TestCli:
    @pytest.fixture
    def schema_file(self, tmp_path: Path) -> Path:
        runner = CliRunner()
        with patch("cosalette._schema._cli._import_app", return_value=_app()):
            result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])
        assert result.exit_code == EXIT_OK
        path = tmp_path / "schema.yaml"
        path.write_text(result.stdout, encoding="utf-8")
        return path

    def test_ha_discovery_instance_id(self, schema_file: Path) -> None:
        result = CliRunner().invoke(
            schema_app, ["ha-discovery", str(schema_file), "--instance-id", "attic"]
        )

        assert result.exit_code == EXIT_OK
        ids = {p["config"]["unique_id"] for p in json.loads(result.stdout)}
        assert ids == {"cosalette_attic_sensor_celsius", "cosalette_attic_bridge"}

    def test_openhab_instance_id(self, schema_file: Path) -> None:
        result = CliRunner().invoke(
            schema_app, ["openhab", str(schema_file), "--instance-id", "attic"]
        )

        assert result.exit_code == EXIT_OK
        assert "Thing mqtt:topic:broker:attic_sensor" in result.stdout

    def test_invalid_instance_id_exits(self, schema_file: Path) -> None:
        result = CliRunner().invoke(
            schema_app, ["ha-discovery", str(schema_file), "--instance-id", "a/b"]
        )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "invalid --instance-id" in result.stderr
