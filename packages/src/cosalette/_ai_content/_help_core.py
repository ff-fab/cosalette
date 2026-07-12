"""Core help topic content for cosalette AI guidance."""

from __future__ import annotations


def _get_core_help_part1(topic: str) -> str | None:
    _dispatch: dict[str, str] = {
        "telemetry": """📡 Telemetry Development Guide

Key Concepts:
  • Declarative device registration via @app.telemetry() decorator
  • Periodic data collection + automatic MQTT publishing
  • Type-based dependency injection + context access
  • Publishing strategies + persistence policies
  • timeout= — per-invocation backstop via asyncio.wait_for; auto-defaults to
    the poll interval; pass timeout=None to disable (see: cosalette ai help resilience)

Common Patterns:
  1. Register devices using @app.telemetry("device_name", interval=seconds)
  2. Return dict from handler — framework publishes automatically
  3. Use init= parameter for dependency injection
  4. Access settings + state via DeviceContext parameter

Example:
  ```python
  import cosalette

  app = cosalette.App(name="mybridge", version="1.0.0")

  @app.telemetry("sensor", interval=30.0)
  async def sensor() -> dict[str, object]:
      return {"temperature": 23.5, "humidity": 65.0}

  @app.telemetry("cpu", interval=10.0, init=make_monitor)
  async def cpu_usage(monitor: CpuMonitor) -> dict[str, object]:
      return {"cpu_percent": monitor.get_usage()}
  ```

Best Practices:
  • Return dict[str, object] from telemetry handlers
  • Use clear, descriptive device names + field names
  • Handle failures gracefully (return None or raise for permanent errors)
  • Access persistent state via ctx.state
  • Use OnChange() publishing to reduce MQTT traffic

Multi-Device Registration:
  • For multiple similar devices, use name=callable (dict-name):
    @app.telemetry(name=lambda s: s.sensors, interval=10)
  • One handler, many devices — framework expands at startup
  • Per-device config injected by type annotation
  • Full guide: cosalette ai help multi-device

Related: cosalette ai help testing, cosalette ai help multi-device""",
        "testing": """🧪 Testing Guide

Activate in conftest.py:
  pytest_plugins = ["cosalette.testing._plugin"]
  # Provides fixtures: mock_mqtt · fake_clock · device_context
  # asyncio_mode = "auto" (set in pyproject.toml) — no @pytest.mark.asyncio needed

Key utilities (from cosalette.testing):
  • AppHarness      — full app integration test harness
  • MockMqttClient  — in-memory MQTT double that records published messages
  • FakeClock       — deterministic clock (no wall-clock delay)
  • make_settings   — factory for test Settings without .env files

Testing layers:
  1. Domain  — pure functions/parsers: plain pytest, zero cosalette imports
  2. Device  — handler logic: use device_context fixture
  3. Integration — full MQTT round-trip: use AppHarness.create()

─────────────────────────────────────────
⚠️  NEVER patch time.monotonic, asyncio.sleep, or time.sleep globally.
asyncio uses these internally. Global patches corrupt loop timing —
in Python 3.14+ this causes an infinite loop or asyncio.timeout failure
before your production code even runs.
─────────────────────────────────────────

Clock and time control:
  • Device coroutines MUST call ctx.sleep(N), not asyncio.sleep() or time.sleep().
    The fake_clock fixture intercepts ctx.sleep(), advancing virtual time
    instantly with no wall-clock delay.
  • FakeClock can also be used directly for domain code:
      clock = FakeClock(0.0)
      clock._time = 42.0   # advance time
      assert clock.now() == 42.0

Module-swap pattern (domain code with time_module reference):
  When domain code uses a module-level alias (e.g. time_module = time),
  swap the MODULE OBJECT — do NOT patch the attribute:

  ```python
  import myapp.domain.device as mod
  import types

  fake = types.SimpleNamespace(monotonic=iter([0.0, 61.0, 61.0, 122.0]).__next__)
  mod.time_module = fake   # ✓ only intercepts calls through this module's reference

  # WRONG — patches time.monotonic globally, also breaks asyncio internals:
  # with mock.patch("myapp.domain.device.time_module.monotonic", side_effect=[...]):
  ```

  Why: time_module is an alias for the standard time module. Patching
  time_module.monotonic is equivalent to patching time.monotonic globally,
  which also affects asyncio's loop.time() calls. In Python 3.14,
  asyncio.timeout() calls loop.time() twice before your coroutine starts,
  consuming mock values intended for your deadline arithmetic.

device_context fixture (device-layer tests):
  ```python
  async def test_sensor(device_context):
      result = await sensor(device_context)
      assert result["value"] > 0
  ```

AppHarness (integration tests):
  ```python
  import asyncio
  import json
  from cosalette.testing import AppHarness

  async def test_app_publishing():
      harness = AppHarness.create()

      @harness.app.telemetry("test", interval=1.0)
      async def test_device():
          return {"value": 42}

      async def trigger_shutdown():
          await asyncio.sleep(0.01)
          harness.shutdown_event.set()

      asyncio.create_task(trigger_shutdown())
      await harness.run()
      messages = harness.mqtt.get_messages_for("testapp/test/state")
      payloads = [json.loads(msg[0]) for msg in messages]
      assert any(p["value"] == 42 for p in payloads)
  ```

AppHarness convenience methods (cos-zo3.5+):
  • published() — access MockMqttClient.published list
  • messages_for(topic) — filter messages by exact topic match
  • last_published() — most recent publish tuple
  • assert_published(topic, contains=..., count=...) — assertion helper
  • assert_state(topic, expected, *, count=...) — deep JSON subset, retained
  • assert_subscribed(topic) — assert exact topic string is in mqtt.subscriptions
  • inject_command(device, payload) — MQTT to {prefix}/{device}/set; str|dict payload
  • call_command(name, payload) — direct @app.command invocation
  • advance_time(seconds) — fast-forward FakeClock

Command testing:
  • inject_command(): MQTT delivery to {prefix}/{device}/set; payload str | dict
    (requires app running)
  • call_command(): Direct handler invocation with typed payloads
    (works without app running)

Related: cosalette ai help configuration, cosalette ai help architecture""",
        "configuration": """⚙️  Configuration Development Guide

Configuration System:
  • Extend cosalette.Settings base class for type-safe configuration
  • Nested MQTT, logging, + schema validation settings
  • Hierarchical: environment variables > .env files > defaults
  • Automatic validation via Pydantic

Custom Settings Pattern:
  ```python
  from cosalette import Settings, App
  from pydantic_settings import SettingsConfigDict

  class MyAppSettings(Settings):
      sensor_port: str = "/dev/ttyUSB0"
      poll_interval: float = 30.0
      calibration_offset: float = 0.0

      model_config = SettingsConfigDict(
          env_prefix="MYAPP_",
          env_nested_delimiter="__"
      )

  app = App(
      name="mybuilding",
      version="1.0.0",
      settings_class=MyAppSettings
  )

  @app.telemetry("sensor", interval=cosalette.setting_ref("poll_interval"))
  async def sensor(ctx: DeviceContext):
      port = ctx.settings.sensor_port
      offset = ctx.settings.calibration_offset
      return {"value": await read_sensor(port) + offset}
  ```

Built-in Settings:
  • MQTT connection: mqtt.host, mqtt.port, mqtt.username, mqtt.password
  • MQTT TLS: mqtt.tls, mqtt.tls_ca_file, mqtt.tls_cert_file, mqtt.tls_key_file
  • Logging: nested under logging.level, logging.format, logging.file
  • Schema enforcement: schema.enforcement, schema.path

Environment Variables:
  • Use MYAPP_ prefix to avoid conflicts
  • .env file support for local development
  • Production overrides via environment

Settings References:
  • Use cosalette.setting_ref() for intervals/enabled flags:
    interval=cosalette.setting_ref("poll_interval")
  • Inspectable by introspection tools (vs. opaque lambda s: s.field)
  • Avoids crashes on --help when settings aren't available
  • Backward-compatible: lambda s: s.field still works

Best Practices:
  • Extend cosalette.Settings, don't create from scratch
  • Access settings via ctx.settings in handlers
  • Use setting_ref() for deferred settings access in registration
  • Validate custom settings + Pydantic constraints

Related: cosalette ai help telemetry""",
        "architecture": """🏗️  Architecture + Design Patterns Guide

Core Design Principles:
  Framework enforces specific architectural patterns → maintainable,
  testable IoT bridge applications.

App as Composition Root:
  • Use App() as single point where all components get wired together
  • Register devices declaratively using decorators in app.py/main.py
  • Avoid imperative component setup scattered across modules
  • Example: @app.telemetry(), @app.command(), @app.device()

Why: Centralized composition makes dependencies explicit + testing easier.
App instance becomes natural boundary for integration tests.

Dependency Injection over Global State:
  • Use init= factories to inject dependencies into handlers
  • Framework inspects type hints + injects matching types
  • Avoid module-level globals or singletons for hardware access
  • Use DeviceContext.state for per-device persistent state

Why: Global state makes testing hard + creates hidden coupling. Type-based
injection makes dependencies explicit in function signatures.

Hexagonal Architecture (Ports & Adapters):
  • Business logic lives in telemetry/command handlers (core)
  • Hardware access happens through adapters (external boundary)
  • Framework provides ports: MqttPort, ClockPort
  • Adapters implement these ports for different environments

Why: Clear separation → easy mocking for tests + swapping
implementations (MockMqttClient vs real broker, SystemClock vs test clock).

Async-First + Graceful Shutdown:
  • All I/O operations must be async
  • Use ctx.sleep() instead of time.sleep() to respect shutdown signals
  • Return None from telemetry for temporary failures (retry)
  • Raise exceptions for permanent failures (stop device)

Why: Async enables efficient I/O multiplexing. Shutdown awareness prevents
zombie processes + enables graceful application termination.

Based on established patterns from:
  • Hexagonal Architecture (Alistair Cockburn)
  • Dependency Injection / IoC containers
  • Actor Model for device coroutines
  • Clean Architecture separation of concerns

Related: cosalette ai help telemetry, cosalette ai help testing""",
    }
    return _dispatch.get(topic)


def _get_core_help_part2(topic: str) -> str | None:
    _dispatch: dict[str, str] = {
        "commands": """🎯 Command Development Guide

Command Handling Patterns:
  • @app.command() decorator for standalone fire-and-forget commands
  • ctx.commands() async iterator for stateful devices + command loops
  • @ctx.on_command() callbacks for simple device-level command handling
  • Sub-topic routing for organized command separation

Core Components:
  • Command dataclass: structured metadata (topic, payload, sub_topic, timestamp)
  • Sub-topic routing: separate handlers for different command types
  • Async iterator pattern: queue-backed commands in @app.device loops

Common Patterns:
  1. Standalone commands for stateless operations
  2. Device command loops + timeout for mixed command/periodic work
  3. Sub-topic routing for command organization

Example:
  ```python
  from cosalette import App, DeviceContext, Command

  app = App(name="controller", version="1.0.0")

  # Standalone fire-and-forget command
  @app.command("reset")
  async def handle_reset(cmd: Command) -> None:
      log.info("Reset command at %s", cmd.timestamp)
      await perform_reset(cmd.payload)

  # Device with command loop
  @app.device("actuator")
  async def actuator_device(ctx: DeviceContext):
      # Sub-topic command handler
      @ctx.on_command("calibrate")
      async def handle_calibrate(topic, payload):
          await run_calibration(payload)

      # Main command loop + timeout for periodic work
      async for cmd in ctx.commands(timeout=60):
          if cmd is None:
              await periodic_maintenance()
          else:
              await process_position_command(cmd.payload)
  ```

Best Practices:
  • Use @app.command() for stateless, fire-and-forget operations
  • Use ctx.commands() in @app.device loops for stateful command handling
  • Sub-topic routing separates command types: device/set vs device/calibrate/set
  • Command dataclass provides structured access to metadata
  • Timeout enables mixed command/periodic patterns

Related: cosalette ai help sub-entities""",
        "health": """🏥 Health Monitoring + Auto-Restart Guide

Health System:
  • HealthCheckable protocol for custom health checks
  • health_check_interval parameter for automatic monitoring
  • Auto-restart on health check failures
  • Per-device availability reporting + app-level status

Core Concepts:
  • App-level status + LWT for crash detection
  • Per-device availability topics, granular monitoring
  • Structured JSON heartbeat + version + device status
  • HealthCheckable protocol for custom health validation

Common Patterns:
  1. Implement HealthCheckable protocol for custom health checks
  2. Set health_check_interval for automatic monitoring
  3. Use app-level + device-level availability reporting

Example:
  ```python
  from cosalette import App, DeviceContext, HealthCheckable

  class DatabaseMonitor(HealthCheckable):
      async def health_check(self) -> tuple[bool, str]:
          try:
              await self.db.execute("SELECT 1")
              return True, "ok"
          except Exception as e:
              return False, f"db_error: {e}"

  app = App(name="monitor", version="1.0.0")

  @app.telemetry("sensor", interval=30.0, health_check_interval=60.0,
                  init=make_monitor)
  async def sensor(monitor: DatabaseMonitor, ctx: DeviceContext) -> dict:
      # Framework automatically calls monitor.health_check() every 60s
      # Auto-restarts device if health check fails
      return {"value": await monitor.read_value()}
  ```

Health Check Behavior:
  • health_check_interval triggers automatic health validation
  • Failed health checks set device availability → "error"
  • Auto-restart attempts to recover from transient failures
  • Device availability goes "offline" → "online" on successful restart
  • App-level heartbeat includes per-device status aggregation

MQTT Topics:
  • {app}/status — App-level status + LWT (offline) + JSON heartbeat
  • {app}/{device}/availability — Per-device availability (online/offline/error)
  • Structured heartbeat includes version + per-device status

Best Practices:
  • Implement HealthCheckable for external dependency monitoring
  • Set appropriate health_check_interval based on criticality
  • Keep health checks lightweight + fast
  • Return descriptive status messages for debugging

Related: cosalette ai help resilience""",
        "scheduling": """⏰ Scheduling + Wall-Clock Alignment Guide

Scheduling Methods:
  • schedule= parameter + cron expressions for wall-clock alignment
  • schedule= callable (CronSpec) for per-device cron schedules with name=callable
  • ctx.sleep_until() for custom time-based scheduling in device loops
  • interval= for traditional fixed-interval polling

Cron Scheduling:
  • Quartz format: second minute hour day month day-of-week [year]
  • Supports full cron syntax: *, ?, ranges, lists, steps, L, W, #
  • Mutually exclusive + interval= parameter

Time Zone Handling:
  • Defaults to system local timezone (container TZ environment)
  • Explicit timezone via tz= parameter for UTC or other zones

Common Patterns:
  1. Day-aligned polling + cron expressions
  2. Wall-clock scheduling + ctx.sleep_until()
  3. Mixed scheduling in device loops

Example:
  ```python
  from cosalette import App, DeviceContext
  import datetime

  app = App(name="scheduler", version="1.0.0")

  # Cron-scheduled telemetry (6 AM and 6 PM daily)
  @app.telemetry("calendar", schedule="0 0 6,18 * * ?")
  async def read_calendar() -> dict[str, object]:
      return await fetch_calendar_events()

  # Custom wall-clock scheduling in device loop
  @app.device("timer")
  async def timer_device(ctx: DeviceContext):
      while not ctx.shutdown_requested:
          # Sleep until next hour boundary
          next_hour = datetime.time(hour=(datetime.datetime.now().hour + 1) % 24)
          await ctx.sleep_until(next_hour)
          await perform_hourly_task()

  # Multiple target times
  @app.device("alerts")
  async def alert_device(ctx: DeviceContext):
      alert_times = [
          datetime.time(8, 0),   # 8:00 AM
          datetime.time(12, 0),  # 12:00 PM
          datetime.time(18, 0),  # 6:00 PM
      ]
      while not ctx.shutdown_requested:
          await ctx.sleep_until(alert_times)  # Sleeps to nearest upcoming time
          await send_daily_alert()
  ```

Cron Expression Examples:
  • "0 0 * * * ?" — Every hour at minute 0
  • "0 */5 * * * ?" — Every 5 minutes
  • "0 0 6,18 * * ?" — 6 AM + 6 PM daily
  • "0 0 0 1 * ?" — First day of every month
  • "0 0 9 * * MON-FRI" — 9 AM weekdays only

Per-Device Schedules:
  When combined with a dict name (name=callable), schedule= can itself be a callable
  that receives the per-device config and returns a cron string or CronSchedule:
  ```python
  @app.telemetry(
      name=lambda s: s.sensors,            # dict[str, SensorConfig]
      schedule=lambda cfg: cfg.cron_expr,  # each device gets its own schedule
  )
  async def sensor(
      ctx: cosalette.DeviceContext, config: SensorConfig
  ) -> dict[str, object]:
      return {"value": await read_sensor(config)}
  ```
  Constraints:
  • Requires name= to be a callable (dict-name form) — static names have no
    per-device config to pass to the callable
  • Cannot combine with group= (coalescing groups require interval=)

Best Practices:
  • Use schedule= for regular time-aligned polling (calendars, daily reports)
  • Use schedule= callable when different devices need different schedules
  • Use ctx.sleep_until() for complex custom scheduling logic
  • interval= remains best for fixed-interval polling
  • Consider timezone implications for wall-clock scheduling
  • First execution runs immediately, then follows schedule

Related: cosalette ai help telemetry, cosalette ai help multi-device""",
        "resilience": """🛡️  Resilience + Error Recovery Guide

Resilience Features:
  • Retry + configurable backoff strategies on @app.telemetry
  • Circuit breaker pattern for persistent failure protection
  • Exponential, linear, + fixed backoff strategies
  • Retry only on specific exception types
  • timeout= per-invocation backstop via asyncio.wait_for

Retry Configuration:
  • retry= parameter sets maximum retry attempts
  • retry_on= specifies which exception types to retry
  • backoff= strategy controls delay between attempts
  • circuit_breaker= prevents cascade failures

Backoff Strategies:
  • ExponentialBackoff: doubles delay each attempt (default)
  • LinearBackoff: increases by fixed step
  • FixedBackoff: constant delay
  • All strategies include ±20% jitter to prevent thundering herd

Common Patterns:
  1. I/O failure retry + exponential backoff
  2. Circuit breaker for persistent external service failures
  3. Custom exception filtering for retryable errors

Example:
  ```python
  from cosalette import App, ExponentialBackoff, CircuitBreaker
  from cosalette import DeviceContext
  import asyncio

  app = App(name="resilient", version="1.0.0")

  # Basic retry configuration
  @app.telemetry("sensor", interval=60,
                  retry=3,                           # Max 3 attempts
                  retry_on=(OSError, TimeoutError))  # I/O and timeout errors
  async def basic_sensor() -> dict[str, object]:
      return await read_flaky_sensor()

  # Advanced resilience with circuit breaker
  @app.telemetry("external", interval=300,
                  retry=5,
                  retry_on=(OSError, ValueError),    # Include parse errors
                  backoff=ExponentialBackoff(
                      base=2.0,                      # 2s, 4s, 8s, 16s, 32s
                      max_delay=60.0                 # Cap at 60 seconds
                  ),
                  circuit_breaker=CircuitBreaker(
                      threshold=5                    # Open after 5 failures
                  ))
  async def external_api(ctx: DeviceContext) -> dict[str, object]:
      # Circuit opens after 5 consecutive exhausted-retry cycles
      # Handler skipped when circuit open, status = "circuit_open"
      return await call_external_api()
  ```

Circuit Breaker States:
  • closed: Normal operation + retries
  • open: Handler skipped, device status "circuit_open"
  • half-open: Single probe attempt after cooldown

Default Retry Behavior:
  • retry_on defaults to (OSError,) — covers I/O + network failures
  • Excludes ValueError by default (may indicate programming errors)
  • Retry counter persists across poll cycles, resets on success
  • Failed retries don't flood error topics — only final failure published

Timeout Backstop:
  timeout= bounds each handler invocation via asyncio.wait_for. A hung adapter
  call raises TimeoutError instead of wedging the poll loop. Because TimeoutError
  is an OSError subclass (PEP 3151), it is automatically logged, error-published,
  and retried by the existing retry + default retry_on=(OSError,) — no extra
  configuration needed.

  Composing example:
    @app.telemetry("sensor", interval=1500, timeout=120, retry=3)
    async def sensor() -> dict[str, object]:
        return await read_slow_hardware()  # killed at 120 s, retried up to 3x

  Defaults:
  • Omitting timeout — auto-defaults to the poll interval
  • timeout=None — disables the backstop entirely
  • Cron-scheduled handlers (schedule=) — no auto-default; set explicitly

Best Practices:
  • Start + retry=3, retry_on=(OSError,) for I/O operations
  • Add ValueError to retry_on only for known transient parse errors
  • Use circuit breaker for external dependencies (APIs, databases)
  • ExponentialBackoff + max_delay prevents unbounded waits
  • Monitor circuit breaker state via device availability/heartbeat

Related: cosalette ai help health, cosalette ai help telemetry""",
    }
    return _dispatch.get(topic)


def get_core_help(topic: str) -> str | None:
    """Return help content for core topics, or None if not matched."""
    result = _get_core_help_part1(topic)
    if result is not None:
        return result
    return _get_core_help_part2(topic)
