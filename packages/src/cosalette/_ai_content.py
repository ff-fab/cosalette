"""Shared AI content for CLI and MCP tools.

Provides curated framework guidance content that's shared between the CLI
help commands and MCP tools. No MCP dependencies - can be imported by
_package_cli.py without requiring fastmcp to be installed.
"""

from __future__ import annotations

import functools
import importlib.metadata
from pathlib import Path

from packaging.version import Version

# Version feature mapping for upgrade guidance
VERSION_FEATURES: dict[str, list[str]] = {
    "0.3.0": [
        "on_configure — dynamic device registration "
        "(see: cosalette ai help configuration)",
        "ctx.commands() — command channel + sub-topic routing "
        "(see: cosalette ai help commands)",
        "HealthCheckable — health monitoring + auto-restart "
        "(see: cosalette ai help health)",
        "sleep_until / schedule= — wall-clock scheduling "
        "(see: cosalette ai help scheduling)",
        "retry/backoff — resilience patterns (see: cosalette ai help resilience)",
        "ctx.sub_entity() — scoped sub-components "
        "(see: cosalette ai help sub-entities)",
    ],
    "0.3.1": [
        "python -m cosalette — universal CLI fallback",
        "MCP server auto-registration in ai init",
    ],
}


def get_version() -> str:
    """Get the cosalette package version."""
    try:
        return importlib.metadata.version("cosalette")
    except Exception:
        return "unknown"


@functools.lru_cache(maxsize=1)
def _get_package_assets_dir() -> Path:
    """Get the path to packaged guidance assets."""
    try:
        import cosalette

        package_path = Path(cosalette.__file__).parent
        return package_path / "assets" / "guidance"
    except ImportError, AttributeError:
        # Fallback if running in development
        return Path(__file__).parent / "assets" / "guidance"


@functools.lru_cache(maxsize=1)
def get_conventions_content() -> str:
    """Get the cosalette framework conventions and patterns instruction content."""
    try:
        assets_dir = _get_package_assets_dir()
        instructions_file = assets_dir / "cosalette.instructions.md"
        if instructions_file.exists():
            return instructions_file.read_text()
        else:
            return (
                "cosalette framework instructions not found. "
                "Run 'cosalette ai init' to install the instruction file."
            )
    except Exception as e:
        return f"Error reading cosalette instructions: {e}"


def get_prime_content() -> str:
    """Get the cosalette framework bootstrap overview for starting development."""
    version_str = get_version()

    return f"""🚀 cosalette v{version_str} — AI Agent Bootstrap

📋 Essential Commands:
   cosalette ai init           Install instruction file + manage AGENTS.md (CLAUDE.md)
   cosalette ai help <topic>   Topic-specific guidance
   cosalette ai init --force   Refresh instruction file, latest templates

⚡ CLI Invocation:
   cosalette                   If installed as script entry point
   python -m cosalette         Universal fallback (always works)
   uv run python -m cosalette  In uv-managed projects

🔌 MCP Server:
   cosalette ai mcp serve      Start MCP server for VS Code

   Register in .vscode/mcp.json:
   {{
     "servers": {{
       "cosalette": {{
         "command": "python",
         "args": ["-m", "cosalette", "ai", "mcp", "serve"]
       }}
     }}
   }}

   Note: 'cosalette ai init' auto-registers if cosalette[mcp] installed

🎯 Framework Patterns:
   • Declarative app composition via App() + decorators
   • @app.telemetry(), @app.command(), @app.device() registration
   • Type-based dependency injection + init= factories
   • Persistent state via DeviceContext.state

📁 Project Structure:
   .github/instructions/       AI agent instruction files (install via 'ai init')
   AGENTS.md                  Auto-managed framework pointer (canonical installs only)
   CLAUDE.md                  Auto-managed framework pointer (if file exists)
   app.py or main.py          App composition root (recommended)
   .env                       Environment configuration

🔗 Key Capabilities:
   • Publishing strategies: OnChange, Every, scheduled intervals
   • Persistence policies: SaveOnChange, SaveOnShutdown
   • Health monitoring + error publishing
   • Settings inheritance from cosalette.Settings
   • Async lifecycle management

📚 Deep Dive Topics:
   cosalette ai help architecture   — Design principles + rationale
   cosalette ai help telemetry      — Device registration patterns
   cosalette ai help testing        — Framework testing strategies
   cosalette ai help configuration  — Settings + environment
   cosalette ai help commands       — Command handling + routing
   cosalette ai help health         — Health monitoring + auto-restart
   cosalette ai help scheduling     — Cron scheduling + wall-clock alignment
   cosalette ai help resilience     — Retry strategies + circuit breakers
   cosalette ai help sub-entities   — Sub-component lifecycle management"""


def get_whats_new_content(from_version: str) -> str:
    """Generate What's New section for versions after from_version.

    Args:
        from_version: Starting version to show features from (exclusive)

    Returns:
        Formatted what's new content, or empty string if invalid/no new features
    """
    try:
        base_version = Version(from_version)
    except Exception:
        return ""  # Invalid version format

    # Find all versions newer than from_version
    newer_versions = []
    for version_str in VERSION_FEATURES:
        try:
            version = Version(version_str)
            if version > base_version:
                newer_versions.append((version, version_str))
        except Exception:
            continue  # Skip invalid versions

    if not newer_versions:
        return ""  # No newer versions found

    # Sort by version (newest first for display)
    newer_versions.sort(reverse=True)

    content_lines = [f"## What's New (since {from_version})", ""]

    for _, version_str in newer_versions:
        features = VERSION_FEATURES[version_str]
        content_lines.append(f"### {version_str}")
        for feature in features:
            content_lines.append(f"- {feature}")
        content_lines.append("")

    return "\n".join(content_lines).rstrip()


def get_help_content(topic: str) -> str:
    """Get cosalette framework guidance for a specific topic.

    Args:
        topic: Help topic (telemetry, testing, configuration, architecture)

    Returns:
        Curated help content for the topic

    Raises:
        ValueError: If topic is not recognized
    """
    if topic == "telemetry":
        return """📡 Telemetry Development Guide

Key Concepts:
  • Declarative device registration via @app.telemetry() decorator
  • Periodic data collection + automatic MQTT publishing
  • Type-based dependency injection + context access
  • Publishing strategies + persistence policies

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

Related: cosalette ai help testing"""

    elif topic == "testing":
        return """🧪 Testing Development Guide

Framework Testing Strategy:
  • Unit tests: Test telemetry handlers + business logic in isolation
  • Integration tests: Use AppHarness for one-liner app testing
  • Dependency injection: Mock external dependencies via init= factories

Key Testing Utilities:
  • cosalette.testing.AppHarness: One-liner setup for integration tests
  • cosalette.MockMqttClient: Underlying MQTT test double
  • cosalette.DeviceContext: Injectable context for unit tests
  • Pytest async support: asyncio_mode = "auto" means no decorator needed

Common Test Patterns:
  1. Unit test handlers directly + mocked dependencies
  2. Integration test app publishing + MockMqttClient
  3. Test context state persistence + settings access
  4. Mock hardware dependencies via init= parameter factories

Example:
  ```python
  import asyncio
  from cosalette.testing import AppHarness

  async def test_sensor_handler():
      # Unit test handler directly
      result = await sensor_temperature()
      assert result["celsius"] > 0

  async def test_app_publishing():
      # Integration test with AppHarness
      harness = AppHarness.create()

      @harness.app.telemetry("test", interval=1.0)
      async def test_device():
          return {"value": 42}

      # Start app, wait for publish, then shutdown
      async def trigger_shutdown():
          await asyncio.sleep(0.01)  # Wait for telemetry
          harness.shutdown_event.set()

      asyncio.create_task(trigger_shutdown())
      await harness.run()
  ```

Best Practices:
  • Test handlers independent of framework
  • Use AppHarness.create() for integration testing (wraps MockMqttClient)
  • Mock external dependencies via dependency injection
  • Test error handling paths (None returns, exceptions)

Related: cosalette ai help configuration"""

    elif topic == "configuration":
        return """⚙️  Configuration Development Guide

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

  @app.telemetry("sensor", interval=app.settings.poll_interval)
  async def sensor(ctx: DeviceContext):
      port = ctx.settings.sensor_port
      offset = ctx.settings.calibration_offset
      return {"value": await read_sensor(port) + offset}
  ```

Built-in Settings:
  • MQTT connection: nested settings under mqtt.host, mqtt.port, mqtt.username
  • Logging: nested under logging.level, logging.format, logging.file
  • Schema enforcement: schema.enforcement, schema.path

Environment Variables:
  • Use MYAPP_ prefix to avoid conflicts
  • .env file support for local development
  • Production overrides via environment

Best Practices:
  • Extend cosalette.Settings, don't create from scratch
  • Access settings via ctx.settings in handlers
  • Use app.settings at decoration time for intervals
  • Validate custom settings + Pydantic constraints

Related: cosalette ai help telemetry"""

    elif topic == "architecture":
        return """🏗️  Architecture + Design Patterns Guide

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

Related: cosalette ai help telemetry, cosalette ai help testing"""

    elif topic == "commands":
        return """🎯 Command Development Guide

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

Related: cosalette ai help sub-entities"""

    elif topic == "health":
        return """🏥 Health Monitoring + Auto-Restart Guide

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

Related: cosalette ai help resilience"""

    elif topic == "scheduling":
        return """⏰ Scheduling + Wall-Clock Alignment Guide

Scheduling Methods:
  • schedule= parameter + cron expressions for wall-clock alignment
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

Best Practices:
  • Use schedule= for regular time-aligned polling (calendars, daily reports)
  • Use ctx.sleep_until() for complex custom scheduling logic
  • interval= remains best for fixed-interval polling
  • Consider timezone implications for wall-clock scheduling
  • First execution runs immediately, then follows schedule

Related: cosalette ai help telemetry"""

    elif topic == "resilience":
        return """🛡️  Resilience + Error Recovery Guide

Resilience Features:
  • Retry + configurable backoff strategies on @app.telemetry
  • Circuit breaker pattern for persistent failure protection
  • Exponential, linear, + fixed backoff strategies
  • Retry only on specific exception types

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

Best Practices:
  • Start + retry=3, retry_on=(OSError,) for I/O operations
  • Add ValueError to retry_on only for known transient parse errors
  • Use circuit breaker for external dependencies (APIs, databases)
  • ExponentialBackoff + max_delay prevents unbounded waits
  • Monitor circuit breaker state via device availability/heartbeat

Related: cosalette ai help health, cosalette ai help telemetry"""

    elif topic == "sub-entities":
        return """🔗 Sub-Entity Context Manager Guide

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

    else:
        available = (
            "telemetry, testing, configuration, architecture, commands, health, "
            "scheduling, resilience, sub-entities"
        )
        raise ValueError(f"Unknown topic: {topic}. Available: {available}")


# Available topics for help
AVAILABLE_TOPICS = [
    "telemetry",
    "testing",
    "configuration",
    "architecture",
    "commands",
    "health",
    "scheduling",
    "resilience",
    "sub-entities",
]
