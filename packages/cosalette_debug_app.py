"""Minimal cosalette app for step-through debugging.

Run via the "Debug Example App" launch configuration to set
breakpoints inside the framework and follow the full lifecycle:

  1. Bootstrap  — settings, logging, adapters, MQTT
  2. Wire       — device contexts, command router, subscriptions
  3. Run        — lifespan startup, heartbeat, device tasks, block
  4. Tear down  — cancel tasks, lifespan teardown, health offline

Suggested breakpoints for first exploration:

  _app.py   → _run_async()          # top of orchestration
  _app.py   → _create_mqtt()        # MQTT client creation
  _app.py   → _start_device_tasks() # device coroutine launch
  _health.py → publish_heartbeat()  # heartbeat publishing
  _app.py   → _heartbeat_loop()     # periodic heartbeat

The script uses MockMqttClient so no broker is needed.
Press Ctrl+C in the terminal to trigger graceful shutdown.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import cosalette
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
)


@app.telemetry("sensor", interval=3.0)
async def read_sensor() -> dict[str, object]:
    """Simulate a sensor — zero-arg handler (framework injects nothing)."""
    temp = 20.0 + random.uniform(-2.0, 2.0)
    return {"temperature": round(temp, 1)}


@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    """Simulate a valve that listens for open/close commands."""

    @ctx.on_command
    async def _handle(_topic: str, payload: str) -> None:
        print(f"[valve] command received: {payload}")
        await ctx.publish_state({"valve_state": payload})

    # Keep the device alive until cancelled.
    await asyncio.Event().wait()


# --- Run -------------------------------------------------------------------

if __name__ == "__main__":
    # Use app.run() with MockMqttClient — no real broker needed.
    # Press Ctrl+C to trigger graceful shutdown via signal handlers.
    app.run(mqtt=MockMqttClient())
