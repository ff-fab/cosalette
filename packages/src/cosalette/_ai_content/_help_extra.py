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
        return """📋 Handler Contracts — Metadata + Runtime Validation

Two complementary layers:

  1. Decorator metadata (summary, state_model, payload_model, behavior, effects)
     → introspection only; appears in manifest / MCP tooling; no runtime effect.

  2. Typed handler annotations + state_model
     → runtime validation and serialization via Pydantic v2 TypeAdapter.

Imports:
  ```python
  from cosalette.di import Depends
  from cosalette.mqtt import Payload, Topic, Message
  # also re-exported: cosalette.Depends, cosalette.Payload, cosalette.Topic,
  #                   cosalette.Message, cosalette.PayloadValidationError,
  #                   cosalette.ReturnValidationError
  ```

Typed Command Handler:
  ```python
  from typing import Annotated
  from pydantic import BaseModel
  from cosalette.mqtt import Payload, Topic
  from cosalette.di import Depends

  class ValveCommand(BaseModel):
      position: int  # 0–100

  class ValveState(BaseModel):
      position: int
      flow_lpm: float

  def get_audit() -> AuditLogger: ...  # synchronous only

  @app.command("valve")
  async def handle_valve(
      cmd: Annotated[ValveCommand, Payload()],      # parsed + validated
      full_topic: Annotated[str, Topic()],          # full MQTT topic string
      msg: Message,                                 # raw topic + payload
      audit: Annotated[AuditLogger, Depends(get_audit)],
  ) -> ValveState:                                  # serialized via Pydantic
      ...
  ```

  Binding rules:
  • param named 'payload' with model type → parsed (no marker needed)
  • param named 'topic' with str type → raw topic (no marker needed)
  • Annotated[Model, Payload()] → parsed regardless of name
  • Annotated[str, Topic()] → full topic string regardless of name
  • Message → raw topic+payload struct
  • Depends(fn) → synchronous dependency (nested deps supported; async rejected —
    async def, async __call__, and callables returning a coroutine all raise)

Typed Telemetry Return:
  ```python
  @app.telemetry("climate", interval=60, state_model=SensorReading)
  async def climate() -> SensorReading:
      return SensorReading(celsius=21.5, humidity=58.0)
  ```

  Return normalization order: return annotation → state_model → dict (as-is)
  Primitives / lists wrap as {"value": ...}. Return None to suppress a cycle.

Typed Triggerable Payload:
  ```python
  from typing import Annotated
  from cosalette.mqtt import Payload

  class RefreshCmd(BaseModel):
      days: int = 7

  @app.telemetry("sensor", interval=300, triggerable=True)
  async def sensor(cmd: Annotated[RefreshCmd | None, Payload()]) -> dict[str, object]:
      days = cmd.days if cmd is not None else 7  # None on scheduled runs
      return {"data": await read_sensor(days=days)}
  ```

Raw Escape Hatch:
  • payload: str            → always raw string (by name)
  • Annotated[str, Payload(raw=True)]  → explicit raw
  • topic: str              → always raw topic (by name)

Validation Errors:
  • PayloadValidationError  → inbound payload fails model validation
  • ReturnValidationError   → return value fails annotation/state_model
  Both are caught by the framework and published to the error topic.

Decorator Metadata (introspection only):
  ```python
  @app.telemetry(
      "sensor", interval=30,
      summary="Temp + humidity",
      state_model=SensorReading,
      payload_model=RefreshCmd,
      behavior=["polls I2C", "filters outliers"],
      effects=["updates HA dashboard"],
  )
  ```

  @app.device also accepts state_model= and payload_model= for contract-metadata
  parity. state_model types the device state channel (used by cosalette schema init);
  payload_model is introspection-only for devices today (no device /set channel is
  emitted yet):
  ```python
  @app.device(
      "valve",
      summary="Motorised valve controller",
      state_model=ValveState,     # types the schema state channel
      payload_model=ValveCommand, # introspection-only (no /set channel emitted)
      behavior=["drives GPIO"],
      effects=["updates HA cover entity"],
  )
  async def valve_controller(ctx: cosalette.DeviceContext):
      ...
      yield
  ```

Related: cosalette ai help telemetry, cosalette ai help commands,
          cosalette ai help manifest, cosalette ai help triggerable"""
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

Each device entry includes:
  • name
  • summary, state_model, payload_model, behavior, effects (if declared)
  Note: state_model types the schema state channel; payload_model is
  introspection-only for devices (no /set channel is emitted today).

## MCP equivalent

    cosalette_manifest("myapp.main:app")

## Notes

- Imports the app module at CLI time (module-level code runs)
- Settings-derived intervals show the field name when setting_ref() is used;
  otherwise show "<deferred>" until bootstrap
- Complement with `cosalette ai help contracts` for metadata authoring"""
    if topic == "react":
        return """⚛️  Domain-Event Reactors — @app.react Guide

Purpose:
  @app.react separates domain state from I/O side-effects.
  State objects collect domain events; reactors handle MQTT + persistence.
  The framework dispatches reactors automatically at execution boundaries.

Core Concepts:
  • @app.state — pure domain object; collects events via drain_events()
  • @app.react — top-level async function receiving DI-injected parameters
  • Reaction boundary — the yield in @app.device; return in @app.telemetry/@app.command
  • events parameter — RESERVED name; framework injects drained list directly

Pattern:

  ```python
  from __future__ import annotations
  from dataclasses import dataclass, field

  import cosalette
  from cosalette._persistence._stores import DeviceStore


  @dataclass
  class RegistryEvent:
      name: str
      sensor_id: int


  @dataclass
  class Registry:
      _events: list[RegistryEvent] = field(default_factory=list, repr=False)

      def assign(self, name: str, sensor_id: int) -> None:
          self._events.append(RegistryEvent(name, sensor_id))

      def drain_events(self) -> list[RegistryEvent]:
          evts, self._events = self._events, []
          return evts


  @dataclass
  class SharedState:
      registry: Registry = field(default_factory=Registry)


  app = cosalette.App(name="mybridge", version="1.0.0")


  @app.state
  def shared_state() -> SharedState:
      return SharedState()


  # drain= points to the method that collects pending events
  @app.react(SharedState, drain=lambda s: s.registry.drain_events())
  async def on_registry_events(
      events: list[RegistryEvent],   # reserved name — injected by framework
      ctx: cosalette.DeviceContext,
      store: DeviceStore,
      state: SharedState,
  ) -> None:
      for event in events:
          payload = {"name": event.name, "id": event.sensor_id}
          await ctx.publish("registry/event", payload)
      store["registry"] = [{"name": e.name, "id": e.sensor_id} for e in events]


  # @app.device handlers MUST be async generators — yield is the reaction boundary
  @app.device("receiver")
  async def receiver(ctx: cosalette.DeviceContext, state: SharedState):
      while not ctx.shutdown_requested:
          reading = await read_sensor()
          state.registry.assign(reading.name, reading.id)
          yield          # reactors fire here before next ctx.sleep
          await ctx.sleep(1.0)
  ```

Reaction Boundaries by Handler Type:
  • @app.device  → after each yield AND once at normal completion
  • @app.stream  → after each item processed AND once at handler exit
  • @app.telemetry → after each successful return
  • @app.command → after each successful return
  No reactor dispatch on cancellation or unhandled exceptions.

drain= Forms:
  • drain=None          → state_instance.drain_events() called structurally
  • drain=lambda s: s.sub.drain_events() → events on a sub-object
  • drain=lambda s: s.pop_events()       → custom method name

BREAKING CHANGE — @app.device Semantics:
  @app.device handlers MUST be async generators. Plain coroutines (return None)
  now raise TypeError at runtime. Convert all @app.device handlers:

  Before (OLD — raises TypeError):
    @app.device("sensor")
    async def sensor(ctx: DeviceContext) -> None:
        while not ctx.shutdown_requested:
            data = await read()
            await ctx.publish_state(data)
            await ctx.sleep(30)

  After (NEW — async generator):
    @app.device("sensor")
    async def sensor(ctx: DeviceContext):
        while not ctx.shutdown_requested:
            data = await read()
            await ctx.publish_state(data)
            yield          # reaction boundary
            await ctx.sleep(30)

Testing Reactors:
  Reactor functions are plain async functions — call them directly:

  ```python
  async def test_on_registry_events() -> None:
      state = SharedState()
      state.registry.assign("room", 42)
      events = state.registry.drain_events()

      ctx = FakeDeviceContext()
      store = MemoryStore().device_store("test")

      await on_registry_events(events=events, ctx=ctx, store=store, state=state)

      assert any(t == "registry/event" for t, _ in ctx.published)
  ```

Registration Error Conditions:
  • StateType not registered via @app.state → ValueError at decoration time
  • Reactor function is not async def → TypeError at decoration time
  • drain=None and state has no drain_events() → AttributeError at runtime

Related: cosalette ai help testing, cosalette ai help architecture"""
    if topic == "router":
        return """🔀 Router — Multi-Module Composition Guide

Key Concepts:
  • Router — composition primitive for organizing related devices/commands
  • Topic prefixing — group related devices under common MQTT segment
  • Tag accumulation — metadata layers for filtering/categorization
  • Module independence — define devices without importing App
  • Testable boundaries — unit test router modules in isolation

Philosophy:
  • App-level decorators (@app.telemetry, @app.command) remain FIRST-CLASS
    for small, single-file applications
  • Router is for production apps that need multi-module organization
  • Not a replacement — a composition primitive for when you need it

When to Use Router:
  • Multi-module projects (sensors.py, controls.py, etc.)
  • Shared libraries exporting device bundles
  • Testable module boundaries
  • Apps with >3 devices or multiple hardware subsystems

Basic Usage:
  ```python
  # sensors.py — router module
  import cosalette

  router = cosalette.Router(prefix="sensors", tags=["environment"])

  @router.telemetry("temperature", interval=30)
  async def temp() -> dict[str, object]:
      return {"celsius": 22.5}

  @router.command("calibrate")
  async def calibrate_cmd(payload: str):
      await perform_calibration()

  # main.py — composition root
  import cosalette
  from myapp import sensors

  app = cosalette.App(name="home2mqtt", version="1.0.0")
  app.include_router(sensors.router)
  ```

  Result: temperature device publishes to home2mqtt/sensors/temperature/state

Topic Prefixing:
  • Router prefix becomes a topic segment: {app}/{prefix}/{device}/state
  • Empty prefix: Router("") — no topic segment, just organizational grouping

Tag Accumulation:
  ```python
  router = cosalette.Router(prefix="env", tags=["monitoring"])

  @router.telemetry("temp", interval=30, tags=["critical"])
  # Final tags: ["monitoring", "critical"]
  ```

  Tags are metadata only — available via manifest for filtering/tooling.

Scoped Adapters:
  ```python
  # sensors.py
  router = cosalette.Router(prefix="sensors")
  router.adapter(SensorPort, "myapp.adapters:I2CSensorAdapter")

  @router.telemetry("temperature", interval=30)
  async def temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
      sensor = ctx.adapter(SensorPort)  # gets I2CSensorAdapter
      return {"celsius": sensor.read()}
  ```

  Router adapters override app-level registrations for that router's devices.

Multiple Router Inclusion:
  Router can be included multiple times with different prefixes for
  multi-instance patterns:

  ```python
  sensor_router = cosalette.Router()

  @sensor_router.telemetry("reading", interval=30)
  async def reading() -> dict[str, object]:
      return {"value": 42}

  app.include_router(sensor_router, prefix="indoor", tags=["environment"])
  app.include_router(sensor_router, prefix="outdoor", tags=["environment"])
  # Result: two devices - indoor/reading and outdoor/reading
  ```

Nested Routers:
  Router.include_router() does NOT exist — routers cannot include other routers.
  Multi-level composition must be done at the App level:

  ```python
  # WRONG — will not work
  outer = cosalette.Router(prefix="building")
  inner = cosalette.Router(prefix="floor1")
  outer.include_router(inner)  # AttributeError: Router has no include_router()

  # CORRECT — include both at App level with single-segment prefixes
  app.include_router(outer, prefix="building")
  app.include_router(inner, prefix="floor1")
  # Result: building/.../state and floor1/.../state
  # Note: prefix must be a single MQTT segment (no '/' allowed).
  # For nested paths, combine Router prefix + include_router prefix:
  #   inner = Router(prefix="floor1") + app.include_router(inner, prefix="building")
  #   -> building/floor1/device/state
  ```

Testing Router Modules:
  ```python
  # tests/unit/test_sensors.py
  from cosalette.testing import AppHarness
  from myapp.sensors import router

  async def test_temperature_publishing():
      harness = AppHarness.create()
      harness.app.include_router(router)
      await harness.run()

      # Use convenience methods for assertions
      harness.assert_published("testapp/sensors/temperature/state", contains="celsius")
  ```

  Router modules can be tested without the full app composition.

Migration From App-Level:
  No migration needed! App-level decorators are still idiomatic for:
  • Quickstart examples
  • Single-file apps
  • Simple bridges (≤3 devices)
  • Learning/prototyping

  Use Router when you need module boundaries, not before.

When NOT to Use Router:
  • Single-file apps — use @app.telemetry directly
  • Simple examples or tutorials — keep it flat
  • When you don't have circular import problems

Router vs @app.on_configure:
  • Router — static module composition, topic prefixes, testable
  • on_configure — dynamic registration, conditional logic, computed values
  • Use both when needed — Router for modules, on_configure for conditionals

API Surface:
  • Router(prefix="", tags=[], lifespan=None)
  • router.telemetry() — same params as @app.telemetry
  • router.command() — same params as @app.command
  • router.device() — same params as @app.device
  • router.adapter() — scoped adapter registration
  • app.include_router() — include router in app

Related: cosalette ai help architecture, cosalette ai help migration"""
    if topic == "migration":
        return """🚀 Migration Guide — Adopting New Patterns

Philosophy:
  • App-level decorators (@app.telemetry, @app.command) remain FIRST-CLASS
  • New patterns are additive, not replacements
  • Migrate only when the new pattern solves your problem
  • Existing code continues to work — no forced rewrites

Router Adoption:
  When: Multi-module apps, circular import problems, testable boundaries
  Not needed for: Single-file apps, quickstart examples, simple bridges

  Before (still idiomatic for small apps):
  ```python
  # main.py
  import cosalette

  app = cosalette.App(name="myapp", version="1.0.0")

  @app.telemetry("sensor", interval=30)
  async def sensor() -> dict[str, object]:
      return {"value": 42}
  ```

  After (for multi-module organization):
  ```python
  # sensors.py
  import cosalette
  router = cosalette.Router(prefix="sensors")

  @router.telemetry("temperature", interval=30)
  async def temp() -> dict[str, object]:
      return {"celsius": 22.5}

  # main.py
  import cosalette
  from myapp import sensors

  app = cosalette.App(name="myapp", version="1.0.0")
  app.include_router(sensors.router)
  ```

Typed Contracts:
  Opt-in: annotate when you want runtime validation; dict works as-is.

  Before (legacy — avoid for user-controlled MQTT topics):
  ```python
  @app.command("valve")
  async def valve(payload: str) -> dict[str, object]:
      data = json.loads(payload)           # no schema validation
      return {"position": data["position"]}  # KeyError if field missing
  ```

  After (with typed contracts):
  ```python
  from typing import Annotated
  from pydantic import BaseModel
  from cosalette.mqtt import Payload

  class ValveCmd(BaseModel):
      position: int

  @app.command("valve")
  async def valve(cmd: Annotated[ValveCmd, Payload()]) -> dict[str, object]:
      return {"position": cmd.position}
  ```

@app.device Async Generator:
  BREAKING in 0.4.0: handlers MUST be async generators (add yield).

  Before (0.3.x):
  ```python
  @app.device("sensor")
  async def sensor(ctx: DeviceContext) -> None:
      while not ctx.shutdown_requested:
          await ctx.publish_state({"value": 42})
          await ctx.sleep(30)
  ```

  After (0.4.0+):
  ```python
  @app.device("sensor")
  async def sensor(ctx: DeviceContext):   # remove -> None
      while not ctx.shutdown_requested:
          await ctx.publish_state({"value": 42})
          yield                           # add yield before sleep
          await ctx.sleep(30)
  ```

  Why: yield marks the reaction boundary for @app.react reactors.

AsyncAPI Manifest:
  No code changes — just add decorator metadata for tooling:

  ```python
  @app.telemetry(
      "sensor", interval=30,
      summary="Temperature + humidity sensor",
      state_model=SensorReading,
      behavior=["polls I2C bus", "averages 3 samples"],
      effects=["triggers HA automation"],
  )
  ```

  Then: `cosalette manifest myapp.main:app` produces introspectable JSON.

Testing Harness Updates:
  New helpers are additive; existing AppHarness API unchanged.

  ```python
  # inject_stream is new — for @app.stream testing
  await harness.inject_stream("barcode", item1, item2, shutdown=True)
  ```

Key Principles:
  • Migrate features individually — no big-bang rewrite
  • App-level decorators stay idiomatic for small apps
  • Router is opt-in for multi-module projects
  • Typed contracts are opt-in per handler
  • Existing tests continue to work

Related: cosalette ai help router, cosalette ai help contracts,
          cosalette ai help testing"""
    return None


@functools.cache
def get_extra_help(topic: str) -> str | None:
    """Return extra help content for specific topics, or None if not matched."""
    result = _get_extra_help_part1(topic)
    if result is not None:
        return result
    result = _get_extra_help_part2(topic)
    if result is not None:
        return result
    return _get_extra_help_part3(topic)


def _get_extra_help_part3(topic: str) -> str | None:
    if topic == "availability":
        return """📡 Transport Availability Signaling

Purpose:
  Standardise the pattern of publishing device availability when a transport
  layer (SSH, BLE, serial, etc.) becomes unreachable.  The framework manages the
  {app}/{device}/availability topic so Home Assistant and other consumers see a
  canonical online/offline signal — no per-handler boilerplate required.

Two Forms:
  1. Static — unavailable_on on @app.command:
     Declare which exception types mean "transport down". The framework catches
     them, suppresses the error, publishes "offline", and logs to the error topic.
     The device automatically recovers (publishes "online") on the next successful
     handler invocation.

  2. Dynamic — ctx.mark_unavailable():
     Call from inside any handler body for conditional unavailability (e.g. a
     pre-flight reachability check). Same auto-recovery semantics apply.

Static Form Example:
  ```python
  from cosalette import App, DeviceContext

  app = App(name="wallpanel", version="1.0.0")

  class SSHError(Exception): ...

  @app.command("display", unavailable_on=(SSHError, TimeoutError))
  async def handle_display(ctx: DeviceContext) -> dict[str, object]:
      result = await ssh_client.read()   # SSHError here → offline published
      return {"brightness": result.brightness}
  ```
  • SSHError or TimeoutError → exception suppressed, "offline" published to
    wallpanel/display/availability, error logged to wallpanel/display/error
  • Next successful call → "online" published automatically

Dynamic Form Example:
  ```python
  @app.command("sensor")
  async def handle_sensor(ctx: DeviceContext) -> dict[str, object]:
      if not await client.is_reachable():
          await ctx.mark_unavailable()   # publishes "offline"
          return {}
      data = await client.read()
      return {"value": data}             # next success → "online" auto-published
  ```

Auto-Recovery:
  After ANY successful command handler invocation (no exception raised, not
  suppressed by unavailable_on), the framework:
  1. Publishes "online" to {app}/{device}/availability
  2. Resets the internal unavailability flag
  This is device-scoped — all command handlers sharing a device name share state.

When to Use Each Form:
  | Situation                              | Use                    |
  |----------------------------------------|------------------------|
  | Specific exception = transport failure | unavailable_on=(...,)  |
  | Pre-flight reachability check          | ctx.mark_unavailable() |
  | Mixed: exception + manual check        | Both together          |

Topic Convention:
  • Named device:  {app}/{device}/availability  (retained, QoS 1)
  • Root device:   {app}/availability           (retained, QoS 1)
  Values: "online" | "offline"

Relationship to HealthCheckRunner:
  HealthCheckRunner fires on a polling schedule (health probes).
  Transport availability signaling fires per command invocation.
  Both publish to the same availability topic — they are complementary.

No-Op Safety:
  ctx.mark_unavailable() is a no-op when no HealthReporter is injected
  (e.g. in tests using the device_context fixture directly).

Orphaned Topic Cleanup (removed entities):
  When a device/telemetry/command is removed from config between restarts, its
  retained state/availability topics would linger on the broker forever (a
  "ghost" entity in Home Assistant). Apps with store= configured clear these
  automatically on the first MQTT connect — an empty retained publish to the
  removed entity's state/availability topics. No-op without a store; only
  state/availability are ever cleared (never /set, status, error, _meta). See
  ADR-048.

Related: cosalette ai help health, cosalette ai help commands,
          cosalette ai help testing"""
    if topic == "persistence":
        return """\U0001f4be Persistence — Store Backends, Default Resolution,
and persist= Policies

Store Backends:
  • NullStore      — no-op; all reads return None; writes silently ignored.
  • MemoryStore    — in-process dict; resets on restart; use in tests.
  • JsonFileStore  — production default; reads/writes a JSON file on disk.
  • SqliteStore    — SQL-backed; suited for larger state or concurrent writers.

Default Store Resolution (new in 0.6.0):
  When `store=` is OMITTED from `App(...)`, the framework auto-creates a
  `JsonFileStore` whose path resolves in priority order:
    1. `<NAME>_STORE_PATH` environment variable — `<NAME>` is the app name
       upper-cased with non-alphanumeric characters replaced by underscores
       (e.g. `my-app` -> `MY_APP_STORE_PATH`, `sensor.hub` -> `SENSOR_HUB_STORE_PATH`)
    2. `$XDG_STATE_HOME/<name>/store.json`         (if XDG_STATE_HOME is set)
    3. `~/.local/state/<name>/store.json`          (universal fallback)

  This makes orphaned retained-topic cleanup (ADR-048) work with **zero config**
  — no `store=` wiring required for most apps.

  Pass `store=None` to explicitly opt out of all persistence:
  ```python
  app = cosalette.App(name="myapp", version="1.0.0", store=None)
  ```

  Pass an explicit `Store` instance or factory callable to override:
  ```python
  from cosalette import JsonFileStore, SqliteStore

  # Explicit path
  app = cosalette.App(name="myapp", version="1.0.0",
                      store=JsonFileStore("/var/lib/myapp/state.json"))

  # Lazy factory (resolved at bootstrap)
  app = cosalette.App(name="myapp", version="1.0.0",
                      store=lambda settings: JsonFileStore(settings.store_path))
  ```

Configurable default backend (new in 0.6.0):
  Override the process-wide default store backend before constructing any App:
  ```python
  import cosalette
  from cosalette import SqliteStore

  cosalette.set_default_store_backend(SqliteStore)  # call once at startup
  app = cosalette.App(name="myapp", version="1.0.0")  # uses SqliteStore
  cosalette.set_default_store_backend(None)  # reset to JsonFileStore
  ```
  Only affects apps where `store=` is omitted. Explicit `store=` arguments are
  unaffected. Not thread-safe — call once at import/startup.

  Note: A startup WARNING is logged when the auto-resolved default store is
  detected as ephemeral inside a container without `<NAME>_STORE_PATH` set,
  AND the app's entity set may vary by config (callable name=/enabled=, or
  @app.on_configure hooks present). Apps with a fixed static entity set do
  not warn and produce no store.json (ADR-048 snapshot write skipped).
  Set the env var to a path on a mounted volume to silence it.

  Explicit opt-out / opt-in (App retained_cleanup= parameter):
    Pass retained_cleanup=False to skip ADR-048 cleanup AND suppress the
    ephemeral WARNING explicitly — the store is kept for persist= device
    state, only cleanup and the warning are disabled. Self-documenting
    escape hatch for an @app.on_configure app that uses the hook for
    non-entity-varying reasons (e.g. config validation only).
    Pass retained_cleanup=True to force cleanup on for an app whose entity
    names are derived from import-time config (not callable name= specs) —
    a case the structural heuristic (has_dynamic_entities) would miss.
    None (default) preserves the existing auto-heuristic unchanged.

persist= Policies:
  Attach to `@app.telemetry` to persist handler state (telemetry-only):
  • `SaveOnPublish`   — saves after every publish cycle.
  • `SaveOnChange`    — saves only when the published value differs from stored.
  • `SaveOnShutdown`  — saves once on graceful shutdown (lowest I/O overhead).

  ```python
  from cosalette import SaveOnChange

  @app.telemetry("sensor", interval=60, persist=SaveOnChange())
  async def sensor() -> dict[str, object]:
      return {"value": await read_sensor()}
  ```

  `persist=` requires a store. The default auto-resolved store satisfies this,
  so `persist=` works out of the box. If you pass `store=None`, registering a
  `persist=` handler raises `ValueError` — pass an explicit `Store` (or omit
  `store=`) to enable persistence.

DeviceStore Injection:
  Handlers that declare a `store: DeviceStore` parameter receive a per-entity
  scoped store (namespaced by device name). Key/value semantics; values are
  JSON-serializable:

  ```python
  from cosalette import DeviceStore

  @app.device("thermostat")
  async def thermostat(ctx: cosalette.DeviceContext, store: DeviceStore):
      last = store.get("last_setpoint", 20.0)
      while not ctx.shutdown_requested:
          setpoint = await read_setpoint()
          if setpoint != last:
              store["last_setpoint"] = setpoint
              last = setpoint
          yield
          await ctx.sleep(30)
  ```

Testing:
  Pass `store=MemoryStore()` for hermetic persistence tests (no disk I/O):
  ```python
  from cosalette import MemoryStore

  harness = AppHarness.create(store=MemoryStore())
  ```

  Pass `store=None` to disable persistence entirely in a test:
  ```python
  harness = AppHarness.create(store=None)
  ```

Related: cosalette ai help availability (orphaned cleanup), ADR-015, ADR-037,
          ADR-048, ADR-049"""
    if topic == "consumer":
        return """\U0001f3e0 x-cosalette-consumer — Consumer Discovery Metadata

What It Is:
  The `x-cosalette-consumer` block is a per-property schema extension carrying
  Home Assistant / OpenHAB discovery metadata (display name, unit, device class,
  etc.). The framework's schema loader reads it into `ConsumerMetadata`, and the
  HA discovery + OpenHAB generators turn it into entities/items.

Producing It (the typed way):
  `cosalette.schema.consumer(**meta)` is the single-source, typed producer.
  Attach its result to a pydantic model field via `Field(json_schema_extra=...)`:

  ```python
  from typing import Annotated
  import pydantic
  from cosalette.schema import consumer

  class CoverState(pydantic.BaseModel):
      position: Annotated[int, pydantic.Field(json_schema_extra=consumer(
          display_name="Cover Position",
          unit="%",
          state_class="measurement",
          icon="mdi:window-shutter",
      ))]
  ```

  Keys are typo-checked under a type checker (ty/pyright) at author time against
  `ConsumerMeta` — a TypedDict whose key set is the single source of truth shared
  with the `ConsumerMetadata` reader (drift-guarded in tests). This is a static
  check only: at runtime the reader silently ignores unknown keys. Prefer this
  over hand-built `{"x-cosalette-consumer": {...}}` dicts.

Semantic Presets:
  `cosalette.schema.temperature(display_name)` and
  `cosalette.schema.percent(display_name, *, icon=None)` wrap `consumer()` for
  the two most common field shapes:

  ```python
  from cosalette.schema import percent, temperature

  temperature("Room Temperature")
  # consumer(display_name=..., device_class="temperature", unit="°C",
  #          state_class="measurement")

  percent("Pump Speed", icon="mdi:pump")
  # consumer(display_name=..., unit="%", state_class="measurement", icon=...)
  ```

  `percent()`'s `icon` is omitted from the emitted metadata (not `None`) when
  not supplied, so output matches a hand-written block exactly.

Key Set:
  display_name, device_class, unit, state_class, icon, read_only.
  Keys-only typing — values are not enum-validated here (HA maps `unit` to
  `unit_of_measurement`).

Regen-Survival:
  The block rides on the field, so it survives schema regeneration via
  `TypeAdapter(model).json_schema()` — no hand re-adding on every regen. The
  loader parses it back into `ConsumerMetadata`, and the HA/OpenHAB generators
  consume it.

Related: cosalette ai help manifest, cosalette ai help contracts, ADR-033"""
    return None
