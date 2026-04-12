"""Package-level CLI for cosalette framework users.

Provides the `cosalette` console script with AI commands for downstream
developers who install cosalette via pip/uv.

This CLI is separate from the application-specific CLI in :mod:`cosalette._cli`
and focuses on bootstrap/guidance commands for developers building apps with
cosalette.

See Also:
    COS-0k3 Phase 2 — Day-one downstream AI bootstrap surface.
"""

from __future__ import annotations

import shutil
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

# ---------------------------------------------------------------------------
# Main CLI app
# ---------------------------------------------------------------------------

app = typer.Typer(help="cosalette — IoT-to-MQTT framework CLI")

# Create AI command group
ai_app = typer.Typer(help="AI agent commands for cosalette development")
app.add_typer(ai_app, name="ai")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _get_package_assets_dir() -> Path:
    """Get the path to packaged guidance assets."""
    try:
        import cosalette

        package_path = Path(cosalette.__file__).parent
        return package_path / "assets" / "guidance"
    except ImportError, AttributeError:
        # Fallback if running in development
        return Path(__file__).parent / "assets" / "guidance"


def _get_version() -> str:
    """Get the cosalette package version."""
    try:
        return version("cosalette")
    except Exception:
        return "unknown"


def _find_instructions_dir() -> Path:
    """Find or suggest the instructions directory in the current repo."""
    # Check for existing .github/instructions directory (Copilot convention)
    github_instructions = Path(".github/instructions")
    if github_instructions.is_dir():
        return github_instructions

    # Check for .github directory (we can create instructions subdir)
    github_dir = Path(".github")
    if github_dir.is_dir():
        return github_instructions

    # Fallback: create .github/instructions
    return github_instructions


def _get_canonical_relative_path(target: Path) -> str:
    """Get a robust relative path to the target from the current working directory.

    Falls back to absolute path if relative calculation fails.
    """
    try:
        # Try to get relative path from current working directory
        return str(target.relative_to(Path.cwd()))
    except ValueError:
        # If target is not under cwd, use absolute path
        return str(target.resolve())


def _is_canonical_default_target(target: Path) -> bool:
    """Check if the target is the canonical default instructions file.

    Returns True only for .github/instructions/cosalette.instructions.md
    """
    try:
        # Normalize paths for comparison
        target_resolved = target.resolve()
        canonical_default = (
            Path.cwd() / ".github" / "instructions" / "cosalette.instructions.md"
        ).resolve()
        return target_resolved == canonical_default
    except OSError:
        # If path resolution fails, be conservative and return False
        return False


def _manage_agent_pointer_block(file_path: Path, canonical_path: str) -> bool:
    """Create or update managed block in agent instruction file.

    Args:
        file_path: Path to AGENTS.md or CLAUDE.md
        canonical_path: Relative path to canonical instructions file

    Returns:
        True if file was modified, False if no changes needed
    """
    marker_begin = "<!-- BEGIN COSALETTE AI SUPPORT v:1 -->"
    marker_end = "<!-- END COSALETTE AI SUPPORT -->"

    content_block = f"""{marker_begin}

## cosalette Framework Support

Framework guidance is maintained in [{canonical_path}]({canonical_path}).

**Refresh guidance:** `cosalette ai init --force`
**Framework overview:** `cosalette ai prime`
**Topic-specific help:** `cosalette ai help <topic>`

{marker_end}"""

    if not file_path.exists():
        # Create new file with the content block
        if file_path.name == "AGENTS.md":
            file_path.write_text(f"""# Agent Instructions

{content_block}
""")
            return True
        else:
            # Don't create CLAUDE.md if it doesn't exist
            return False

    current_content = file_path.read_text()

    # Find existing managed block
    begin_idx = current_content.find(marker_begin)
    end_idx = current_content.find(marker_end)

    if begin_idx != -1 and end_idx != -1:
        # Replace existing block
        end_idx = end_idx + len(marker_end)
        new_content = (
            current_content[:begin_idx] + content_block + current_content[end_idx:]
        )
    else:
        # Append new block
        if current_content.strip():
            new_content = current_content + f"\n\n{content_block}\n"
        else:
            new_content = content_block + "\n"

    if new_content != current_content:
        file_path.write_text(new_content)
        return True

    return False


# ---------------------------------------------------------------------------
# AI Commands
# ---------------------------------------------------------------------------


@ai_app.command("init")
def ai_init(
    target: Annotated[
        Path | None,
        typer.Option(
            "--target",
            "-t",
            help="Target path for instruction file (default: "
            ".github/instructions/cosalette.instructions.md)",
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite existing instruction file")
    ] = False,
) -> None:
    """Install or refresh cosalette framework guidance for AI agents and tools."""

    if target is None:
        instructions_dir = _find_instructions_dir()
        target = instructions_dir / "cosalette.instructions.md"

    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    # Check if file exists and not forcing
    if target.exists() and not force:
        typer.echo(f"❌ Instruction file already exists: {target}")
        typer.echo("   Use --force to overwrite, or specify a different --target")
        raise typer.Exit(1)

    # Get the template content
    assets_dir = _get_package_assets_dir()
    template_path = assets_dir / "cosalette.instructions.md"

    if not template_path.exists():
        typer.echo(f"❌ Template not found: {template_path}")
        typer.echo(
            "   This may indicate a packaging issue or development setup problem."
        )
        raise typer.Exit(1)

    # Copy template to target location
    try:
        # Check if this is a refresh (target exists before copy)
        is_refresh = target.exists()

        shutil.copy2(template_path, target)
        status = "✅ Refreshed" if is_refresh else "✅ Installed"
        typer.echo(f"{status} cosalette instructions: {target}")

        # Only auto-manage AGENTS.md/CLAUDE.md when installing canonical default
        if _is_canonical_default_target(target):
            # Get robust relative path to canonical instructions file
            canonical_path = _get_canonical_relative_path(target)

            # Manage agent pointer blocks
            agents_path = Path("AGENTS.md")
            claude_path = Path("CLAUDE.md")
            agents_updated = _manage_agent_pointer_block(agents_path, canonical_path)
            claude_updated = _manage_agent_pointer_block(claude_path, canonical_path)

            # Report pointer block updates
            if agents_updated:
                typer.echo("✅ Updated AGENTS.md pointer block")
            if claude_updated:
                typer.echo("✅ Updated CLAUDE.md pointer block")
            elif Path("CLAUDE.md").exists():
                typer.echo("ℹ️  CLAUDE.md exists but no updates needed")
        else:
            typer.echo(
                "📝 Custom target path - skipping AGENTS.md/CLAUDE.md auto-management"
            )

        typer.echo()
        if _is_canonical_default_target(target):
            typer.echo("Next steps:")
            typer.echo(
                "  • Customize the instruction file for your project's specific needs"
            )
            typer.echo(
                "  • Run 'cosalette ai prime' for framework overview and patterns"
            )
            typer.echo(
                "  • Run 'cosalette ai help <topic>' for topic-specific guidance"
            )
        else:
            typer.echo("Next steps:")
            typer.echo(
                "  • Add framework guidance to your AGENTS.md/CLAUDE.md manually "
                "if needed"
            )
            typer.echo(
                "  • Run 'cosalette ai prime' for framework overview and patterns"
            )
            typer.echo(
                "  • Run 'cosalette ai help <topic>' for topic-specific guidance"
            )

    except Exception as e:
        typer.echo(f"❌ Failed to install instruction file: {e}")
        raise typer.Exit(1) from e


@ai_app.command("prime")
def ai_prime() -> None:
    """Print concise downstream agent/developer bootstrap summary."""

    version_str = _get_version()

    typer.echo(f"""
🚀 cosalette v{version_str} — AI Agent Bootstrap Guide

📋 Essential Commands:
   cosalette ai init           Install instruction file + manage AGENTS.md \
                               (CLAUDE.md if exists)
   cosalette ai help <topic>   Get topic-specific guidance
   cosalette ai init --force   Refresh instruction file with latest templates

🎯 Framework Patterns:
   • Declarative app composition via App() and decorators
   • @app.telemetry(), @app.command(), @app.device() registration
   • Type-based dependency injection with init= factories
   • Persistent state via DeviceContext.state

📁 Project Structure:
   .github/instructions/       AI agent instruction files \
                               (install via 'cosalette ai init')
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
   cosalette ai help configuration  — Settings and environment
""")


@ai_app.command("help")
def ai_help(
    topic: Annotated[
        str, typer.Argument(help="Help topic (telemetry, testing, configuration)")
    ],
) -> None:
    """Print curated topic help for downstream app development."""

    # Map of available topics to their content
    topics = {
        "telemetry": _get_telemetry_help,
        "testing": _get_testing_help,
        "configuration": _get_configuration_help,
        "architecture": _get_architecture_help,
    }

    if topic not in topics:
        available = ", ".join(topics.keys())
        typer.echo(f"❌ Unknown topic: {topic}")
        typer.echo(f"   Available topics: {available}")
        raise typer.Exit(1)

    # Print the topic help
    help_func = topics[topic]
    help_func()


# ---------------------------------------------------------------------------
# Alias Commands (top-level shortcuts)
# ---------------------------------------------------------------------------


@app.command("init", hidden=True)
def init_alias(
    target: Annotated[
        Path | None,
        typer.Option("--target", "-t", help="Target path for instruction file"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite existing instruction file")
    ] = False,
) -> None:
    """Alias for 'cosalette ai init'."""
    ai_init(target=target, force=force)


@app.command("prime", hidden=True)
def prime_alias() -> None:
    """Alias for 'cosalette ai prime'."""
    ai_prime()


# ---------------------------------------------------------------------------
# Version and info
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    version_flag: Annotated[
        bool, typer.Option("--version", "-v", help="Show version and exit")
    ] = False,
) -> None:
    """cosalette framework CLI."""

    if version_flag:
        typer.echo(f"cosalette v{_get_version()}")
        raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Topic help functions
# ---------------------------------------------------------------------------


def _get_telemetry_help() -> None:
    """Print telemetry development guidance."""
    typer.echo("""
📡 Telemetry Development Guide

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

Related: cosalette ai help testing
""")


def _get_testing_help() -> None:
    """Print testing development guidance."""
    typer.echo("""
🧪 Testing Development Guide

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

      @harness.app.telemetry(\"test\", interval=1.0)
      async def test_device():
          return {\"value\": 42}

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

Related: cosalette ai help configuration
""")


def _get_configuration_help() -> None:
    """Print configuration development guidance."""
    typer.echo("""
⚙️  Configuration Development Guide

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

Related: cosalette ai help telemetry
""")


def _get_architecture_help() -> None:
    """Print architectural patterns and design rationale."""
    typer.echo("""
🏗️  Architecture and Design Patterns Guide

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

Related: cosalette ai help telemetry, cosalette ai help testing
""")


# ---------------------------------------------------------------------------
# Console script entry point
# ---------------------------------------------------------------------------


def main_cli() -> None:
    """Entry point for the cosalette console script."""
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("\n❌ Interrupted", err=True)
        sys.exit(1)
    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main_cli()
