"""Extra (non-core) help topic content for cosalette AI guidance."""

from __future__ import annotations

import functools


def _get_extra_help_part1(topic: str) -> str | None:
    if topic == "multi-device":
        return """🔧 Multi-Device Registration Guide

Key Concepts:
  • Dict-name decorators: name=callable on @app.telemetry, @app.device, @app.command
  • One handler, many devices — framework expands at startup
  • Per-device config injected by type via DI
  • Per-device intervals via callable interval=
  • Per-device cron schedules via callable schedule= (requires name=callable)
  • Conditional registration via callable enabled=

Idiomatic Mindset:
  • cosalette favors declarative decorators over imperative loops
  • Prefer name=callable (dict-name) for multiple similar devices
  • Reserve @app.on_configure + add_telemetry() for complex conditional logic
  • Never hand-roll for loops at decoration time

Basic Example:
  ```python
  from dataclasses import dataclass
  import cosalette

  @dataclass
  class SensorConfig:
      mac: str
      location: str = ""

  app = cosalette.App(name="sensors", version="1.0.0")

  @app.telemetry(
      name=lambda s: {
          "living_room": SensorConfig(mac="AA:BB:CC:DD:EE:01"),
          "bedroom":     SensorConfig(mac="AA:BB:CC:DD:EE:02"),
      },
      interval=10,
  )
  async def sensor(
      ctx: cosalette.DeviceContext, config: SensorConfig,
  ) -> dict[str, object]:
      return {"temperature": await read_ble(config.mac)}
  ```

Per-Device Intervals:
  ```python
  @app.telemetry(
      name=lambda s: s.sensors,  # dict[str, SensorConfig]
      interval=lambda cfg: cfg.poll_seconds,
  )
  async def sensor(
      ctx: cosalette.DeviceContext, config: SensorConfig
  ) -> dict[str, object]:
      return {"value": await read_ble(config.mac)}
  ```

Per-Device Schedules:
  When combined with a dict name, schedule= also accepts a CronSpec callable that
  receives the per-device config and returns a cron string or CronSchedule instance.
  This gives each device its own wall-clock schedule:
  ```python
  @dataclass
  class SensorConfig:
      mac: str
      cron_expr: str = "0 0 * * * ?"  # default: every hour

  @app.telemetry(
      name=lambda s: s.sensors,            # dict[str, SensorConfig]
      schedule=lambda cfg: cfg.cron_expr,  # per-device cron schedule
  )
  async def sensor(
      ctx: cosalette.DeviceContext, config: SensorConfig
  ) -> dict[str, object]:
      return {"value": await read_ble(config.mac)}
  ```
  Constraints:
  • schedule= callable requires name= to also be a callable (dict-name form)
  • Incompatible with group= (coalescing groups need a shared interval)
  • Incompatible with schedule= string/CronSchedule + interval= as usual

Settings-Driven Example:
  ```python
  # settings.py
  from cosalette import Settings
  from dataclasses import dataclass

  @dataclass
  class SensorConfig:
      mac: str
      poll_seconds: float = 10.0

  class MySettings(Settings):
      sensors: dict[str, SensorConfig]

  # app.py
  @app.telemetry(
      name=lambda s: s.sensors,
      interval=lambda cfg: cfg.poll_seconds,
  )
  async def sensor(config: SensorConfig) -> dict[str, object]:
      return {"temperature": await read_ble(config.mac)}
  ```

Return Types:
  • dict[str, config] — device names mapped to per-device config
  • list[str] — device names only (no per-device config injection)
  • Callable receives Settings instance, returns name mapping

When to Use What:
  • name=callable — multiple similar devices, config-driven
  • @app.on_configure — complex conditional setup, computed values
  • Static decorator — single device, fixed name

Works With:
  • @app.telemetry() — periodic data collection
  • @app.device() — full lifecycle coroutines
  • @app.command() — command handlers
  • All decorators support name=callable pattern consistently

Related: cosalette ai help telemetry, cosalette ai help configuration"""
    if topic == "sub-entities":
        return """\U0001f517 Sub-Entity Context Manager Guide

Sub-Entity System:
  • ctx.sub_entity() context manager for temporary sub-components
  • Scoped state publishing + command handling
  • Automatic availability lifecycle management
  • Sub-topic command routing integration

Use Cases:
  • Calibration procedures that appear/disappear dynamically
  • Per-sensor staleness tracking within multi-sensor device
  • Temporary functional modes + separate command interfaces
  • Runtime sub-component lifecycle management

Core Features:
  • Scoped availability: {device}/{sub}/availability
  • Scoped state publishing: {device}/{sub}/state
  • Sub-topic command handling: {device}/{sub}/set
  • Automatic cleanup on context exit

Common Patterns:
  1. Temporary calibration sub-entities during procedures
  2. Persistent sub-entities for device lifetime
  3. Dynamic sub-components based on runtime conditions

Example:
  ```python
  from cosalette import App, DeviceContext

  app = App(name="controller", version="1.0.0")

  @app.device("cover")
  async def cover_device(ctx: DeviceContext):
      # Persistent sub-entity for temperature monitoring
      async with ctx.sub_entity("temperature") as temp:
          while not ctx.shutdown_requested:
              # Sub-entity state publishing
              reading = await read_temp_sensor()
              await temp.publish_state({"celsius": reading})
              await ctx.sleep(60)

  @app.device("sensor")
  async def sensor_device(ctx: DeviceContext):
      while not ctx.shutdown_requested:
          if needs_calibration():
              # Temporary calibration sub-entity
              async with ctx.sub_entity("calibrate") as cal:
                  # Sub-topic command handler
                  @cal.on_command
                  async def handle_cal_cmd(topic, payload):
                      await process_calibration_step(payload)

                  # Calibration procedure + progress updates
                  for step in calibration_steps:
                      await cal.publish_state({"step": step, "progress": step/total})
                      await perform_calibration_step(step)
                      await ctx.sleep(1)
              # Sub-entity automatically goes offline here

          await ctx.sleep(300)  # Check every 5 minutes
  ```

Sub-Entity Lifecycle:
  • Context enter: publishes {device}/{sub}/availability = "online"
  • During: scoped state publishing + command handling
  • Context exit: clears retained state, publishes availability = "offline"
  • Automatic cleanup prevents stale broker state

MQTT Topic Structure:
  • State: {app}/{device}/{sub}/state
  • Commands: {app}/{device}/{sub}/set
  • Availability: {app}/{device}/{sub}/availability
  • Follows device topic conventions extended by one level

Naming Restrictions:
  • No MQTT special characters: /, +, #
  • No reserved names: state, set, availability, status, error, config
  • No concurrent duplicates on same device
  • Empty names rejected

When to Use Sub-Entities vs Separate Devices:
  • Sub-entities: temporary or logically grouped components
  • Separate devices: independent lifecycle + state management
  • Sub-entities share parent device's connection + lifecycle
  • Use for calibration modes, sensor breakouts, functional states

Best Practices:
  • Use descriptive sub-entity names (calibrate, sensor1, mode_setup)
  • Keep sub-entity state focused + minimal
  • Prefer separate devices for truly independent components
  • Sub-entity availability inherits from parent on crash (LWT covers device)

Related: cosalette ai help commands, cosalette ai help configuration"""
    if topic == "triggerable":
        return """\U0001f3af Triggerable Telemetry Guide

Concept:
  • Add triggerable=True to @app.telemetry() for on-demand triggered execution
  • Handler runs on interval AND immediately on {prefix}/{device}/set message
  • Opt-in TriggerPayload injectable distinguishes scheduled vs triggered runs
  • Trigger events coalesce — latest payload wins if handler is busy

Basic Usage:
  ```python
  @app.telemetry("sensor", interval=300, triggerable=True)
  async def sensor() -> dict[str, object]:
      return {"temperature": await read_sensor()}
  ```

  The framework subscribes to {prefix}/sensor/set. Any message fires the
  handler immediately. The 300-second interval continues in parallel.

Accessing Trigger Context:
  ```python
  from cosalette import TriggerPayload

  @app.telemetry("sensor", interval=300, triggerable=True)
  async def sensor(trigger: TriggerPayload) -> dict[str, object]:
      days = trigger.get("days", 7) if trigger.is_triggered else 7
      return {"data": await read_sensor(days=days)}
  ```

  TriggerPayload fields:
  • is_triggered: bool — True when fired by MQTT, False on scheduled run
  • raw: str | None — raw MQTT payload string (None on scheduled run)
  • data: dict | None — parsed JSON payload (None if not valid JSON)
  • get(key, default) — convenience accessor for data dict

Imperative Registration:
  ```python
  app.add_telemetry("sensor", handler, interval=300, triggerable=True)
  ```

Constraints:
  • Root (unnamed) devices cannot be triggerable — no topic
    segment to subscribe to
  • triggerable= and group= are mutually exclusive —
    coalescing groups use shared tick-aligned scheduling incompatible with
    on-demand triggers
  • Both constraints raise ValueError at registration time

Coalescing:
  • Multiple MQTT messages before handler completes → only latest payload used
  • Handler runs once with most recent TriggerPayload, not once per message
  • Prevents thundering-herd on burst triggers

Best Practices:
  • Use for long-interval sensors that need on-demand refresh
  • Declare TriggerPayload only when you need to distinguish trigger source
  • Keep trigger handler logic identical to scheduled logic — just with payload access
  • Combine with publish strategies (OnChange, Every) — they apply to triggered runs too

Related: cosalette ai help telemetry, cosalette ai help commands"""
    return None


def _get_extra_help_part2(topic: str) -> str | None:
    if topic == "contracts":
        return """📋 Contract-First API Design Guide

Key Concepts:
  • Contract metadata adds semantic clarity to telemetry, command, and device
    registrations
  • Summary, state/payload models, and behavior/effects descriptions for documentation
  • Introspection exposes metadata for tooling and manifest generation
  • Metadata is informational only — no runtime enforcement

Common Patterns:
  1. Add summary= for human-readable descriptions
  2. Use state_model=/payload_model= to document expected types
  3. Document operational steps with behavior= and side effects with effects=
  4. View contracts via registry snapshot or manifest tools

Examples:
  ```python
  @app.telemetry(
      "sensor",
      interval=30,
      summary="Temperature and humidity readings",
      state_model=SensorReading,
      payload_model=SensorCommand,  # For triggerable telemetry
      behavior=["polls I2C sensor", "filters outliers", "caches last value"],
      effects=["triggers calibration alerts"]
  )
  async def sensor() -> dict[str, object]:
      return {"temp_c": 23.5, "humidity": 65.0}

  @app.command(
      "valve",
      summary="Opens or closes irrigation valve",
      state_model=ValveState,
      payload_model=ValveCommand,
      behavior=["validates flow constraints", "logs to audit trail"],
      effects=["mutates valve position", "updates flow metrics"]
  )
  async def valve(payload: dict[str, object]) -> dict[str, object]:
      return {"status": "opened", "flow_rate": 2.5}

  @app.device(
      "receiver",
      summary="Serial receiver: read sensor frames and publish state",
      behavior=["opens serial port", "reads LaCrosse frames",
                "dispatches per-sensor state"],
      effects=["publishes to {name}/state"]
  )
  async def receiver(ctx: DeviceContext) -> None:
      ...
  ```

Introspection:
  • build_registry_snapshot() includes all metadata fields
  • Models serialized as class names in JSON output
  • Behavior/effects lists remain as-is for JSON compatibility

Best Practices:
  • Keep summaries concise but descriptive
  • Use behavior= for operational steps (both telemetry and commands)
  • Use effects= for side effects and mutations (both telemetry and commands)
  • Models can be Pydantic types, dataclasses, or plain classes
  • All metadata appears in manifests and development tooling

Related: cosalette ai help telemetry, cosalette ai help commands"""
    if topic == "manifest":
        return """cosalette manifest — App Registry Manifest
==========================================

The manifest command prints the resolved registration surface of a cosalette
app as JSON or a human-readable table.

## Usage

    cosalette manifest myapp.main:app           # JSON output
    cosalette manifest myapp.main:app --table   # human-readable table

The JSON output is the same structure as `cosalette_inspect_app` (MCP).

## Output fields

Each telemetry entry includes:
  • name, interval (or field name if setting_ref() is used), strategy, persist
  • triggerable flag
  • summary, state_model, payload_model, behavior, effects (if declared)

Each command entry includes:
  • name, mqtt_params, enabled
  • summary, state_model, payload_model, behavior, effects (if declared)

## MCP equivalent

    cosalette_manifest("myapp.main:app")

## Notes

- Imports the app module at CLI time (module-level code runs)
- Settings-derived intervals show the field name when setting_ref() is used;
  otherwise show "<deferred>" until bootstrap
- Complement with `cosalette ai help contracts` for metadata authoring"""
    return None


@functools.cache
def get_extra_help(topic: str) -> str | None:
    """Return extra help content for specific topics, or None if not matched."""
    result = _get_extra_help_part1(topic)
    if result is not None:
        return result
    return _get_extra_help_part2(topic)
