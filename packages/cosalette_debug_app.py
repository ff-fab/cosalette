"""Minimal cosalette app for step-through debugging.

Run via the "Debug Example App" launch configuration to set
breakpoints inside the framework and follow the full lifecycle:

  1. Bootstrap  — settings, logging, adapters, MQTT
  2. Wire       — device contexts, command router, subscriptions
  3. Run        — lifespan startup, heartbeat, telemetry groups,
                  device tasks, persistence load, block
  4. Tear down  — persistence save, cancel tasks, lifespan teardown,
                  health offline

Suggested breakpoints for first exploration:

  _app.py              → _run_async()             # top of orchestration
  _wiring.py           → create_mqtt()            # MQTT client creation
  _wiring.py           → start_device_tasks()     # device + telemetry launch
  _telemetry_runner.py → run_telemetry_group()    # coalescing group scheduler
  _stores.py           → DeviceStore.load/save    # per-device persistence
  _health.py           → publish_heartbeat()      # heartbeat publishing
  Pt1Filter.update()   (Rust)                     # filter step-through

The script uses MockMqttClient so no broker is needed.
Press Ctrl+C in the terminal to trigger graceful shutdown.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import cosalette
from cosalette import (
    DeviceContext,
    DeviceStore,
    MemoryStore,
    OnChange,
    Pt1Filter,
    SaveOnChange,
)
from cosalette.testing import MockMqttClient

# --- App assembly ----------------------------------------------------------


@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    """Lifespan — startup code runs before yield, teardown after."""
    print(f"[lifespan] startup — settings loaded: {type(ctx.settings).__name__}")
    yield
    print("[lifespan] shutdown — cleaning up")


app = cosalette.App(
    name="debugapp",
    version="0.1.0-debug",
    heartbeat_interval=10.0,  # short interval for debugging
    lifespan=lifespan,
    store=MemoryStore(),  # in-memory persistence (no files needed)
)


# --- Telemetry (coalescing group "sensors") --------------------------------


def make_filter() -> Pt1Filter:
    """Factory — returned value is injected into the handler by type."""
    return Pt1Filter(tau=1.0, dt=3.0)


@app.telemetry(
    "sensor_a",
    interval=3.0,
    init=make_filter,  # DI: Pt1Filter injected into handler
    publish=OnChange(),  # publish only when value changes
    persist=SaveOnChange(),  # persist DeviceStore on value change
    group="sensors",  # coalesced with sensor_b
)
async def read_sensor(filt: Pt1Filter, store: DeviceStore) -> dict[str, object]:
    """Filtered sensor — demonstrates init, publish strategy, persistence."""
    raw = 20.0 + random.uniform(-2.0, 2.0)
    count: int = store.get("count") or 0  # type: ignore[assignment]
    store["count"] = count + 1
    return {"temperature": round(filt.update(raw), 1), "readings": store["count"]}


@app.telemetry("sensor_b", interval=3.0, group="sensors")
async def read_humidity() -> dict[str, object]:
    """Second sensor in the same coalescing group."""
    return {"humidity": round(50.0 + random.uniform(-5.0, 5.0), 1)}


# --- Device (full-lifecycle coroutine) -------------------------------------


@app.device("valve")
async def valve_loop(ctx: DeviceContext) -> None:
    """Full-lifecycle device — publishes state and handles commands inline."""
    state = "closed"

    @ctx.on_command
    async def _on_cmd(sub_topic: str | None, payload: str) -> None:  # noqa: ARG001
        nonlocal state
        state = payload
        print(f"[valve] command: {payload}")

    while not ctx.shutdown_requested:
        await ctx.publish_state({"valve_state": state})
        await ctx.sleep(5.0)


# --- Run -------------------------------------------------------------------

if __name__ == "__main__":
    # Use app.run() with MockMqttClient — no real broker needed.
    # Press Ctrl+C to trigger graceful shutdown via signal handlers.
    app.run(mqtt=MockMqttClient())
