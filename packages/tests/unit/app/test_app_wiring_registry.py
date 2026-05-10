"""Tests for cosalette App — publish_registry_snapshot (AsyncAPI registry publication).

Covers: retained JSON publication, fire-and-forget error handling,
payload size warnings, and snapshot content for populated apps.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._mqtt import MqttPort
from tests.unit.conftest import _DummyImpl, _DummyPort

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TestPublishRegistrySnapshot — registry snapshot MQTT publication
# ---------------------------------------------------------------------------


class TestPublishRegistrySnapshot:
    """Tests for :func:`cosalette._wiring.publish_registry_snapshot`.

    Test Techniques Used:
        - Specification-based Testing: Verifies topic, retain flag, QoS,
          and payload structure.
        - Error-handling Testing: Ensures fire-and-forget semantics
          when MQTT publish raises.
    """

    @pytest.mark.anyio
    async def test_publishes_asyncapi_as_retained_json(self) -> None:
        """Canonical AsyncAPI document is published as retained JSON to
        _meta/registry.
        """
        from unittest.mock import AsyncMock

        from cosalette._wiring import publish_registry_snapshot

        # Arrange
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/testapp"

        # Act
        await publish_registry_snapshot(app, mqtt, prefix)

        # Assert
        mqtt.publish.assert_awaited_once()
        call_args = mqtt.publish.call_args
        topic = call_args.args[0]
        payload = call_args.args[1]
        retain = call_args.kwargs["retain"]
        qos = call_args.kwargs["qos"]

        assert topic == "cosalette/testapp/_meta/registry"
        assert retain is True
        assert qos == 1

        # Payload must be a pre-serialized JSON string (fixes double serialization)
        import json

        assert isinstance(payload, str)
        parsed = json.loads(payload)
        assert parsed["asyncapi"] == "3.0.0"
        assert parsed["info"]["title"] == "testapp"
        assert parsed["info"]["version"] == "1.0.0"
        assert "x-cosalette-contract-version" in parsed["info"]

    @pytest.mark.anyio
    async def test_logs_and_continues_on_publish_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Publish failure is logged but not raised (fire-and-forget)."""
        from unittest.mock import AsyncMock

        from cosalette._wiring import publish_registry_snapshot

        # Arrange
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        mqtt.publish.side_effect = RuntimeError("broker down")
        prefix = "cosalette/testapp"

        # Act — should not raise
        with caplog.at_level(logging.ERROR, logger="cosalette._wiring"):
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert
        assert "Failed to publish" in caplog.text

    @pytest.mark.anyio
    async def test_logs_and_continues_on_asyncapi_build_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """AsyncAPI build failure is logged but not raised (fire-and-forget).

        Technique: Error Guessing — verifying the full fire-and-forget
        contract covers document construction, not only MQTT publish.
        """
        from unittest.mock import AsyncMock, patch

        from cosalette._wiring import publish_registry_snapshot

        # Arrange
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/testapp"

        with (
            patch.object(app, "asyncapi", side_effect=RuntimeError("asyncapi failed")),
            caplog.at_level(logging.ERROR, logger="cosalette._wiring"),
        ):
            # Act — should not raise
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert
        assert "Failed to publish" in caplog.text
        mqtt.publish.assert_not_awaited()

    @pytest.mark.anyio
    async def test_warns_when_payload_exceeds_size_threshold(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A WARNING is logged when the serialized payload exceeds 128 KiB.

        Technique: Boundary Value Analysis — payload just over the
        ``_REGISTRY_PAYLOAD_WARN_BYTES`` threshold triggers a warning
        while publishing still proceeds (advisory only).
        """
        from unittest.mock import AsyncMock, patch

        from cosalette._wiring import (
            _REGISTRY_PAYLOAD_WARN_BYTES,
            publish_registry_snapshot,
        )

        # Arrange — build an AsyncAPI dict that serializes above the threshold
        oversized_doc = {
            "asyncapi": "3.0.0",
            "info": {"title": "testapp", "version": "1.0.0"},
            "padding": "x" * (_REGISTRY_PAYLOAD_WARN_BYTES + 1),
        }
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/testapp"

        with (
            patch.object(app, "asyncapi", return_value=oversized_doc),
            caplog.at_level(logging.WARNING, logger="cosalette._wiring"),
        ):
            # Act
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert — warning was emitted
        assert "large payloads may exceed broker max_packet_size" in caplog.text
        assert str(_REGISTRY_PAYLOAD_WARN_BYTES) in caplog.text

        # Assert — publish still happened (advisory only)
        mqtt.publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_no_warning_when_payload_at_or_below_threshold(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No WARNING is logged when the payload is at or below 128 KiB.

        Technique: Boundary Value Analysis — complement to the above-threshold
        test; payload exactly at ``_REGISTRY_PAYLOAD_WARN_BYTES`` must NOT
        trigger a warning.
        """
        import json as _json
        from unittest.mock import AsyncMock, patch

        from cosalette._wiring import (
            _REGISTRY_PAYLOAD_WARN_BYTES,
            publish_registry_snapshot,
        )

        shell: dict[str, object] = {
            "asyncapi": "3.0.0",
            "info": {"title": "t", "version": "0"},
            "padding": "",
        }
        overhead = len(_json.dumps(shell, separators=(",", ":")).encode("utf-8"))
        fill_size = _REGISTRY_PAYLOAD_WARN_BYTES - overhead
        shell["padding"] = "x" * fill_size

        app = App(name="t", version="0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/t"

        with (
            patch.object(app, "asyncapi", return_value=shell),
            caplog.at_level(logging.WARNING, logger="cosalette._wiring"),
        ):
            # Act
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert — no warning for at-threshold payload
        assert "large payloads" not in caplog.text
        mqtt.publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_populated_app_snapshot_includes_all_registrations(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Snapshot from an app with devices, telemetry, command, and adapter.

        Technique: Specification-based Testing — verifies the publish
        payload reflects real registrations (not just empty lists from a
        bare ``App``).  Also confirms no spurious size warning for a small
        payload.
        """
        from unittest.mock import AsyncMock

        from cosalette._wiring import publish_registry_snapshot

        # Arrange — build an app with diverse registrations
        app = App(name="myapp", version="2.0.0")

        @app.device("blind")
        async def _blind(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
            pass  # pragma: no cover

        @app.telemetry("temperature", interval=60)
        async def _temperature() -> dict[str, object]:
            return {"value": 21.5}  # pragma: no cover

        @app.command("set_mode")
        async def _set_mode(topic: str, payload: str) -> None:
            pass  # pragma: no cover

        app.adapter(_DummyPort, _DummyImpl)

        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/myapp"

        with caplog.at_level(logging.WARNING, logger="cosalette._wiring"):
            # Act
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert — no spurious size warning for a small populated app
        assert "large payloads" not in caplog.text
        mqtt.publish.assert_awaited_once()
        payload_dict_raw = mqtt.publish.call_args.args[1]
        import json

        assert isinstance(payload_dict_raw, str)
        payload_dict = json.loads(payload_dict_raw)

        # Canonical AsyncAPI 3.0.0 structure
        assert payload_dict["asyncapi"] == "3.0.0"
        assert payload_dict["info"]["title"] == "myapp"
        assert payload_dict["info"]["version"] == "2.0.0"

        channels = payload_dict.get("channels", {})
        # Device channel: blindState
        assert "blindState" in channels
        assert channels["blindState"]["x-cosalette-archetype"] == "device"
        # Telemetry channel: temperatureState
        assert "temperatureState" in channels
        assert channels["temperatureState"]["x-cosalette-archetype"] == "telemetry"
        # Command channel stripped by security redaction (_asyncapi_doc_for_broker)
        assert "set_modeCommand" not in channels
