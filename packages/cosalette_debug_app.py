"""Minimal cosalette app for step-through debugging.

Run via the "Debug Example App" launch configuration to set
breakpoints inside the framework and follow the full lifecycle:

  1. Bootstrap  — settings, logging, adapters, MQTT
  2. Wire       — device contexts, command router, subscriptions
  3. Run        — startup hooks, heartbeat, device tasks, block
  4. Tear down  — cancel tasks, shutdown hooks, health offline

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
import signal

import cosalette
from cosalette.testing import MockMqttClient

# --- App assembly ----------------------------------------------------------

app = cosalette.App(
    name="debugapp",
    version="0.1.0-debug",
    heartbeat_interval=10.0,  # short interval for debugging
)


@app.telemetry("sensor", interval=3.0)
async def read_sensor(_ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Simulate a sensor that returns temperature readings."""
    temp = 20.0 + random.uniform(-2.0, 2.0)
    return {"temperature": round(temp, 1)}


@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    """Simulate a valve that listens for open/close commands."""

    @ctx.on_command
    async def handle(_topic: str, payload: str) -> None:
        print(f"[valve] command received: {payload}")
        await ctx.publish_state({"valve_state": payload})

    # Keep the device alive until cancelled.
    await asyncio.Event().wait()


@app.on_startup
async def startup(ctx: cosalette.AppContext) -> None:
    """Startup hook — runs after MQTT connect, before devices."""
    print(f"[hook] startup — settings loaded: {type(ctx.settings).__name__}")


@app.on_shutdown
async def shutdown(_ctx: cosalette.AppContext) -> None:
    """Shutdown hook — runs after devices stop, before disconnect."""
    print("[hook] shutdown — cleaning up")


# --- Run -------------------------------------------------------------------

if __name__ == "__main__":
    # Use _run_async directly with a MockMqttClient so we don't
    # need a real MQTT broker.  A manual shutdown event lets us
    # trigger graceful teardown with Ctrl+C.
    mock_mqtt = MockMqttClient()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        print("\n[signal] Shutdown requested")
        shutdown_event.set()

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)

        # Set a breakpoint on the next line to step into the framework:
        await app._run_async(  # noqa: SLF001
            shutdown_event=shutdown_event,
            mqtt=mock_mqtt,
        )

    asyncio.run(main())
