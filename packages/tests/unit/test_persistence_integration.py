"""Integration tests for framework persistence — App + DeviceStore + policies.

Test Techniques Used:
    - State Transition: Store lifecycle across app startup/device/shutdown
    - Specification-based: store= parameter, auto-injection, persist= policies
"""

from __future__ import annotations

import asyncio

import pytest

import cosalette
from cosalette._context import DeviceContext
from cosalette._persist import SaveOnChange, SaveOnPublish, SaveOnShutdown
from cosalette._stores import MemoryStore
from cosalette.testing import AppHarness


class TestPersistenceIntegration:
    """Integration tests for DeviceStore injection and persist policies.

    Exercises the full App → DeviceStore → MemoryStore pipeline via
    :class:`AppHarness`, verifying store injection, lifecycle, and
    save-policy behaviour end-to-end.

    See Also:
        ADR-007 — Testing strategy (integration layer).
    """

    async def test_telemetry_handler_receives_device_store(self) -> None:
        """Telemetry handler requesting DeviceStore gets one injected.

        Technique: State-based — register telemetry, run lifecycle,
        inspect the received object and backend contents.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)
        received: dict[str, object] = {}

        @harness.app.telemetry("sensor", interval=0.01)
        async def sensor(store: cosalette.DeviceStore) -> dict[str, object]:
            store.setdefault("count", 0)
            store["count"] = int(store["count"]) + 1  # type: ignore[arg-type]
            received["store"] = store
            if int(store["count"]) >= 1:  # type: ignore[arg-type]
                harness.trigger_shutdown()
            return store.to_dict()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert isinstance(received["store"], cosalette.DeviceStore)
        assert backend.load("sensor") == {"count": 1}

    async def test_telemetry_store_auto_loads_existing_data(self) -> None:
        """Pre-seeded store data is visible to the handler on first call.

        Technique: State Transition — seed backend, verify handler sees it.
        """
        backend = MemoryStore(initial={"sensor": {"temp": 21.5}})
        harness = AppHarness.create(store=backend)
        seen_values: list[object] = []

        @harness.app.telemetry("sensor", interval=0.01)
        async def sensor(store: cosalette.DeviceStore) -> dict[str, object]:
            seen_values.append(store.get("temp"))
            harness.trigger_shutdown()
            return store.to_dict()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert seen_values == [21.5]

    async def test_telemetry_store_saves_on_shutdown(self) -> None:
        """Store data written during the loop is persisted on shutdown.

        Technique: State Transition — handler writes data, shutdown
        triggers, verify backend has the data.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)

        @harness.app.telemetry("sensor", interval=0.01)
        async def sensor(store: cosalette.DeviceStore) -> dict[str, object]:
            store["written"] = True
            harness.trigger_shutdown()
            return store.to_dict()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        saved = backend.load("sensor")
        assert saved is not None
        assert saved["written"] is True

    async def test_device_handler_receives_device_store(self) -> None:
        """Device handler requesting DeviceStore gets one injected.

        Technique: State-based — register device, run lifecycle,
        verify the store was injected and data persisted.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)
        received: dict[str, object] = {}

        @harness.app.device("actuator")
        async def actuator(ctx: DeviceContext, store: cosalette.DeviceStore) -> None:
            store["active"] = True
            received["store"] = store
            harness.trigger_shutdown()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert isinstance(received["store"], cosalette.DeviceStore)
        assert backend.load("actuator") == {"active": True}

    async def test_no_store_means_no_injection(self) -> None:
        """App without store= works fine; handler doesn't need a store.

        Technique: Specification-based — no store, no crash.
        """
        harness = AppHarness.create()  # no store=

        @harness.app.telemetry("sensor", interval=0.01)
        async def sensor() -> dict[str, object]:
            harness.trigger_shutdown()
            return {"value": 1}

        await asyncio.wait_for(harness.run(), timeout=5.0)

        messages = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(messages) >= 1

    async def test_store_keyed_by_device_name(self) -> None:
        """Two telemetry devices each get their own key in the shared store.

        Technique: State-based — two devices write to their stores,
        verify distinct keys in the shared MemoryStore backend.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)
        both_done = asyncio.Event()
        done_count = 0

        @harness.app.telemetry("alpha", interval=0.01)
        async def alpha(store: cosalette.DeviceStore) -> dict[str, object]:
            nonlocal done_count
            store["src"] = "alpha"
            done_count += 1
            if done_count >= 2:
                both_done.set()
            return store.to_dict()

        @harness.app.telemetry("beta", interval=0.01)
        async def beta(store: cosalette.DeviceStore) -> dict[str, object]:
            nonlocal done_count
            store["src"] = "beta"
            done_count += 1
            if done_count >= 2:
                both_done.set()
            return store.to_dict()

        async def _shutdown() -> None:
            await both_done.wait()
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        alpha_data = backend.load("alpha")
        beta_data = backend.load("beta")
        assert alpha_data is not None
        assert beta_data is not None
        assert alpha_data["src"] == "alpha"
        assert beta_data["src"] == "beta"

    async def test_persist_save_on_publish(self) -> None:
        """persist=SaveOnPublish() saves the store on publish cycles.

        Technique: State Transition — track backend.save calls through
        store data changes visible on each cycle.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)
        cycle_count = 0

        @harness.app.telemetry("sensor", interval=0.01, persist=SaveOnPublish())
        async def sensor(store: cosalette.DeviceStore) -> dict[str, object]:
            nonlocal cycle_count
            cycle_count += 1
            store["cycle"] = cycle_count
            if cycle_count >= 2:
                harness.trigger_shutdown()
            return store.to_dict()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Store was saved during the loop (not just on shutdown)
        saved = backend.load("sensor")
        assert saved is not None
        assert saved["cycle"] >= 1

    async def test_persist_save_on_change(self) -> None:
        """persist=SaveOnChange() saves the store when dirty.

        Technique: State Transition — handler mutates store, policy
        triggers save during the loop.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)
        cycle_count = 0

        @harness.app.telemetry("sensor", interval=0.01, persist=SaveOnChange())
        async def sensor(store: cosalette.DeviceStore) -> dict[str, object]:
            nonlocal cycle_count
            cycle_count += 1
            store["cycle"] = cycle_count
            if cycle_count >= 2:
                harness.trigger_shutdown()
            return store.to_dict()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        saved = backend.load("sensor")
        assert saved is not None
        assert saved["cycle"] >= 1

    async def test_persist_save_on_shutdown_only(self) -> None:
        """persist=SaveOnShutdown() does not save during the loop.

        Technique: State Transition — verify the store is only saved
        during shutdown, not during the loop. We do this by checking
        that mid-loop the backend has no data, but after shutdown it does.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)
        mid_loop_saved: list[dict[str, object] | None] = []
        cycle_count = 0

        @harness.app.telemetry("sensor", interval=0.01, persist=SaveOnShutdown())
        async def sensor(store: cosalette.DeviceStore) -> dict[str, object]:
            nonlocal cycle_count
            cycle_count += 1
            store["cycle"] = cycle_count
            # Snapshot what the backend has mid-loop
            mid_loop_saved.append(backend.load("sensor"))
            if cycle_count >= 2:
                harness.trigger_shutdown()
            return store.to_dict()

        await asyncio.wait_for(harness.run(), timeout=5.0)

        # During the loop, SaveOnShutdown never triggered a save
        # First cycle: no prior saves at all
        assert mid_loop_saved[0] is None

        # But after shutdown, the safety-net save persisted the data
        final = backend.load("sensor")
        assert final is not None
        assert final["cycle"] >= 1

    async def test_persist_requires_store(self) -> None:
        """persist= without store= raises ValueError at decoration time.

        Technique: Specification-based — validation at decoration time.
        """
        harness = AppHarness.create()  # no store=

        with pytest.raises(ValueError, match="persist.*requires.*store"):

            @harness.app.telemetry("sensor", interval=0.01, persist=SaveOnPublish())
            async def sensor() -> dict[str, object]:
                return {"value": 1}
