"""Shared AI content for CLI and MCP tools.

Provides curated framework guidance content that's shared between the CLI
help commands and MCP tools. No MCP dependencies - can be imported by
_package_cli.py without requiring fastmcp to be installed.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path


def get_version() -> str:
    """Get the cosalette package version."""
    try:
        return importlib.metadata.version("cosalette")
    except Exception:
        return "unknown"


def _get_package_assets_dir() -> Path:
    """Get the path to packaged guidance assets."""
    try:
        import cosalette

        package_path = Path(cosalette.__file__).parent
        return package_path / "assets" / "guidance"
    except ImportError, AttributeError:
        # Fallback if running in development
        return Path(__file__).parent / "assets" / "guidance"


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

    return f"""🚀 cosalette v{version_str} — AI Agent Bootstrap Guide

📋 Essential Commands:
   cosalette ai init           Install instruction file + manage AGENTS.md (CLAUDE.md)
   cosalette ai help <topic>   Get topic-specific guidance
   cosalette ai init --force   Refresh instruction file with latest templates

🎯 Framework Patterns:
   • Declarative app composition via App() and decorators
   • @app.telemetry(), @app.command(), @app.device() registration
   • Type-based dependency injection with init= factories
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
   • Health monitoring and error publishing
   • Settings inheritance from cosalette.Settings
   • Async lifecycle management

📚 Deep Dive Topics:
   cosalette ai help architecture   — Design principles and rationale
   cosalette ai help telemetry      — Device registration patterns
   cosalette ai help testing        — Framework testing strategies
   cosalette ai help configuration  — Settings and environment"""


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
  • Periodic data collection with automatic MQTT publishing
  • Type-based dependency injection and context access
  • Publishing strategies and persistence policies

Common Patterns:
  1. Register devices using @app.telemetry("device_name", interval=seconds)
  2. Return dict from handler - framework publishes automatically
  3. Use init= parameter for dependency injection
  4. Access settings and state via DeviceContext parameter

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
  • Use clear, descriptive device names and field names
  • Handle failures gracefully (return None or raise for permanent errors)
  • Access persistent state via ctx.state
  • Use OnChange() publishing to reduce MQTT traffic

Related: cosalette ai help testing"""

    elif topic == "testing":
        return """🧪 Testing Development Guide

Framework Testing Strategy:
  • Unit tests: Test telemetry handlers and business logic in isolation
  • Integration tests: Use AppHarness for one-liner app testing
  • Dependency injection: Mock external dependencies via init= factories

Key Testing Utilities:
  • cosalette.testing.AppHarness: One-liner setup for integration tests
  • cosalette.MockMqttClient: Underlying MQTT test double
  • cosalette.DeviceContext: Injectable context for unit tests
  • Pytest async support: @pytest.mark.asyncio for async handlers

Common Test Patterns:
  1. Unit test handlers directly with mocked dependencies
  2. Integration test app publishing with MockMqttClient
  3. Test context state persistence and settings access
  4. Mock hardware dependencies via init= parameter factories

Example:
  ```python
  import asyncio
  import pytest
  from cosalette.testing import AppHarness

  @pytest.mark.asyncio
  async def test_sensor_handler():
      # Unit test handler directly
      result = await sensor_temperature()
      assert result["celsius"] > 0

  @pytest.mark.asyncio
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
  • Test handlers independently of the framework
  • Use AppHarness.create() for integration testing (wraps MockMqttClient)
  • Mock external dependencies via dependency injection
  • Test error handling paths (None returns, exceptions)

Related: cosalette ai help configuration"""

    elif topic == "configuration":
        return """⚙️  Configuration Development Guide

Configuration System:
  • Extend cosalette.Settings base class for type-safe configuration
  • Nested MQTT, logging, and schema validation settings
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
  • Validate custom settings with Pydantic constraints

Related: cosalette ai help telemetry"""

    elif topic == "architecture":
        return """🏗️  Architecture and Design Patterns Guide

Core Design Principles:
  The framework enforces specific architectural patterns to ensure maintainable,
  testable IoT bridge applications.

App as Composition Root:
  • Use App() as the single point where all components are wired together
  • Register devices declaratively using decorators in app.py/main.py
  • Avoid imperative component setup scattered across modules
  • Example: @app.telemetry(), @app.command(), @app.device()

Why: Centralized composition makes dependencies explicit and testing easier.
The App instance becomes the natural boundary for integration tests.

Dependency Injection over Global State:
  • Use init= factories to inject dependencies into handlers
  • Framework inspects type hints and injects matching types
  • Avoid module-level globals or singletons for hardware access
  • Use DeviceContext.state for per-device persistent state

Why: Global state makes testing hard and creates hidden coupling. Type-based
injection makes dependencies explicit in function signatures.

Hexagonal Architecture (Ports & Adapters):
  • Business logic lives in telemetry/command handlers (core)
  • Hardware access happens through adapters (external boundary)
  • Framework provides ports (interfaces) like MqttPort, ClockPort
  • Adapters implement these ports for different environments

Why: Clear separation enables easy mocking for tests and swapping
implementations (MockMqttClient vs real broker, SystemClock vs test clock).

Async-First with Graceful Shutdown:
  • All I/O operations must be async
  • Use ctx.sleep() instead of time.sleep() to respect shutdown signals
  • Return None from telemetry for temporary failures (retry)
  • Raise exceptions for permanent failures (stop device)

Why: Async enables efficient I/O multiplexing. Shutdown awareness prevents
zombie processes and enables graceful application termination.

Based on established patterns from:
  • Hexagonal Architecture (Alistair Cockburn)
  • Dependency Injection / IoC containers
  • Actor Model for device coroutines
  • Clean Architecture separation of concerns

Related: cosalette ai help telemetry, cosalette ai help testing"""

    else:
        available = "telemetry, testing, configuration, architecture"
        raise ValueError(f"Unknown topic: {topic}. Available topics: {available}")


# Available topics for help
AVAILABLE_TOPICS = ["telemetry", "testing", "configuration", "architecture"]
