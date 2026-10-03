"""Availability concurrency regression tests.

Techniques: State Transition Testing and Error Guessing — pause MQTT I/O
to reproduce recovery, outage, propagation, and reconnect interleavings.
"""

from __future__ import annotations

import asyncio
from typing import Any, override

import pytest

from cosalette._health import HealthReporter
from cosalette.testing import FakeClock, MockMqttClient

pytestmark = pytest.mark.unit


class _PausedMqtt(MockMqttClient):
    """Pause one selected publication before recording it."""

    def __init__(self) -> None:
        super().__init__()
        self.pause: tuple[str, str] | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    @override
    async def publish(
        self,
        topic: str,
        payload: str | dict[str, Any],
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        if (topic, payload) == self.pause:
            self.pause = None
            self.entered.set()
            await self.release.wait()
        await super().publish(topic, payload, retain=retain, qos=qos)


@pytest.fixture
def paused_health(fake_clock: FakeClock) -> tuple[HealthReporter, _PausedMqtt]:
    """Wire a stream and its fed sensor to a controllable broker."""
    mqtt = _PausedMqtt()
    health = HealthReporter(mqtt, "app", "1", fake_clock)
    health.set_feeds("feed", ["sensor"])
    health.track_freshness("feed", 10, label="Stream")
    health.set_device_status("feed")
    health.set_device_status("sensor")
    return health, mqtt


@pytest.mark.parametrize("paused_entity", ["feed", "sensor"])
async def test_fresh_item_during_offline_publish_recovers_feed_and_sensor(
    paused_health: tuple[HealthReporter, _PausedMqtt],
    fake_clock: FakeClock,
    paused_entity: str,
) -> None:
    """Recovery waits for both the stream publish and its propagation."""
    health, mqtt = paused_health
    fake_clock.advance(10)
    mqtt.pause = (f"app/{paused_entity}/availability", "offline")

    stale = asyncio.create_task(health.check_freshness())
    await asyncio.wait_for(mqtt.entered.wait(), 1)
    recovery = asyncio.create_task(health.record_success("feed"))
    await asyncio.sleep(0)
    mqtt.release.set()
    await asyncio.wait_for(asyncio.gather(stale, recovery), 1)

    assert not health.is_unavailable("feed")
    assert not health.is_unavailable("sensor")
    assert health.heartbeat_payload().devices["feed"].status == "ok"
    assert health.heartbeat_payload().devices["sensor"].status == "ok"
    for entity in ("feed", "sensor"):
        assert mqtt.get_messages_for(f"app/{entity}/availability") == [
            ("offline", True, 1),
            ("online", True, 1),
        ]


@pytest.mark.parametrize("paused_entity", ["feed", "sensor"])
async def test_outage_during_recovery_publish_preserves_latest_offline(
    paused_health: tuple[HealthReporter, _PausedMqtt],
    paused_entity: str,
) -> None:
    """The reverse interleaving keeps manual outage and fed state offline."""
    health, mqtt = paused_health
    await health.publish_device_unavailable("feed", source="supervisor")
    mqtt.published.clear()
    mqtt.pause = (f"app/{paused_entity}/availability", "online")

    recovery = asyncio.create_task(health.clear_task_failure("feed"))
    await asyncio.wait_for(mqtt.entered.wait(), 1)
    outage = asyncio.create_task(health.publish_device_unavailable("feed"))
    await asyncio.sleep(0)
    mqtt.release.set()
    await asyncio.wait_for(asyncio.gather(recovery, outage), 1)

    assert health.is_unavailable("feed", source="manual")
    assert not health.is_unavailable("feed", source="supervisor")
    assert health.is_unavailable("sensor", source="stream:feed")
    for entity in ("feed", "sensor"):
        assert mqtt.get_messages_for(f"app/{entity}/availability") == [
            ("online", True, 1),
            ("offline", True, 1),
        ]


@pytest.mark.parametrize("announcement", ["announce", "reannounce"])
async def test_outage_during_announcement_keeps_retained_offline(
    paused_health: tuple[HealthReporter, _PausedMqtt],
    announcement: str,
) -> None:
    """First connect and reconnect cannot publish an old state last."""
    health, mqtt = paused_health
    mqtt.pause = ("app/feed/availability", "online")
    announcing = asyncio.create_task(
        health.announce_device("feed")
        if announcement == "announce"
        else health.reannounce()
    )
    await asyncio.wait_for(mqtt.entered.wait(), 1)
    outage = asyncio.create_task(health.publish_device_unavailable("feed"))
    await asyncio.sleep(0)
    mqtt.release.set()
    await asyncio.wait_for(asyncio.gather(announcing, outage), 1)

    assert health.is_unavailable("feed")
    assert health.is_unavailable("sensor", source="stream:feed")
    for entity in ("feed", "sensor"):
        assert mqtt.get_messages_for(f"app/{entity}/availability")[-1] == (
            "offline",
            True,
            1,
        )


async def test_concurrent_stream_recovery_preserves_other_sensor_source(
    paused_health: tuple[HealthReporter, _PausedMqtt],
) -> None:
    """Independent stream and manual sources stay separate under contention."""
    health, mqtt = paused_health
    health.set_feeds("other", ["sensor"])
    await health.publish_device_unavailable("feed")
    await health.publish_device_unavailable("other")
    await health.publish_device_unavailable("sensor")

    await asyncio.gather(
        health.publish_device_available("feed"),
        health.publish_device_available("other"),
    )

    assert health.is_unavailable("sensor", source="manual")
    assert not health.is_unavailable("sensor", source="stream:feed")
    assert not health.is_unavailable("sensor", source="stream:other")
    assert mqtt.get_messages_for("app/sensor/availability") == [
        ("offline", True, 1),
    ]
    await health.publish_device_available("sensor")
    assert mqtt.get_messages_for("app/sensor/availability")[-1] == (
        "online",
        True,
        1,
    )


async def test_freshness_rechecks_age_after_waiting_for_reannounce(
    paused_health: tuple[HealthReporter, _PausedMqtt],
    fake_clock: FakeClock,
) -> None:
    """A queued stale check cannot mark data stale after a newer item."""
    health, mqtt = paused_health
    fake_clock.advance(10)
    mqtt.pause = ("app/feed/availability", "online")
    announcing = asyncio.create_task(health.reannounce())
    await asyncio.wait_for(mqtt.entered.wait(), 1)
    stale = asyncio.create_task(health.check_freshness())
    await asyncio.sleep(0)
    await health.record_success("feed")
    mqtt.release.set()

    _, newly_stale = await asyncio.wait_for(asyncio.gather(announcing, stale), 1)

    assert newly_stale == []
    assert not health.is_unavailable("feed")
    assert not health.is_unavailable("sensor")
    assert health.heartbeat_payload().devices["feed"].status == "ok"
    assert all(payload != "offline" for _, payload, _, _ in mqtt.published)
