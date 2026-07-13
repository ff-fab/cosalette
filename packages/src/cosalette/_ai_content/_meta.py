"""Shared AI content metadata and short content functions.

Provides metadata (topics list, version features), version retrieval, and
shorter content functions like conventions, prime, and what's new.
"""

from __future__ import annotations

import functools
import importlib.metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

# Available topics for help — must stay in sync with get_help_content() branches
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
    "triggerable",
    "multi-device",
    "contracts",
    "manifest",
    "react",
    "router",
    "migration",
    "availability",
    "persistence",
]

# Version feature mapping for upgrade guidance
VERSION_FEATURES: dict[str, list[str]] = {
    "0.3.0": [
        "name=callable — declarative multi-device registration "
        "(see: cosalette ai help multi-device)",
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
    "0.3.2": [
        "triggerable= — on-demand MQTT-triggered telemetry "
        "(see: cosalette ai help triggerable)",
    ],
    "0.3.3": [
        "enabled=callable — deferred settings-derived enabled flag on decorators "
        "(see: cosalette ai help multi-device)",
        "store=callable — lazy store resolution at bootstrap",
    ],
    "0.3.4": [
        "setting_ref() — inspectable settings bindings "
        "(see: cosalette ai help configuration)",
        "Contract metadata — summary, state_model, payload_model, behavior, effects "
        "on telemetry/command (see: cosalette ai help contracts)",
        "cosalette manifest — print app registry manifest as JSON or table "
        "(see: cosalette ai help manifest)",
    ],
    "0.3.7": [
        "Contract metadata — summary, behavior, effects on @app.device() "
        "and add_device() (see: cosalette ai help contracts)",
    ],
    "0.3.10": [
        "Per-device callable schedule= — when name=callable, schedule= also accepts "
        "a CronSpec callable (per_device_config) -> str | CronSchedule, giving each "
        "device its own cron schedule (see: cosalette ai help scheduling, "
        "cosalette ai help multi-device)",
    ],
    "0.3.12": [
        "MQTT TLS client settings — mqtt.tls, mqtt.tls_ca_file, and mutual-TLS "
        "cert/key fields (see: cosalette ai help configuration)",
        "MCP server is stdio-only; SSE transport is intentionally unsupported "
        "for local dynamic import safety",
    ],
    "0.5.0": [
        "unavailable_on on @app.command — transport availability signaling: "
        "declare which exceptions mark the device offline; framework suppresses, "
        "publishes offline to availability topic, and auto-recovers on next success "
        "(see: cosalette ai help availability)",
        "ctx.mark_unavailable() — dynamic availability control from inside handlers; "
        "same auto-recovery semantics (see: cosalette ai help availability)",
        "AppHarness.assert_state() / assert_subscribed() — deep JSON subset + retain "
        "check, subscription assertion (see: cosalette ai help testing)",
        "AppHarness.inject_command() accepts str | dict payload — dict auto-serialized "
        "(see: cosalette ai help testing)",
        "Orphaned retained-topic cleanup — apps with store= configured automatically "
        "clear state/availability retained topics for entities removed from config "
        "since the last run, on the first MQTT connect; prevents ghost entities in "
        "Home Assistant (see: cosalette ai help availability, ADR-048)",
        "timeout= on @app.telemetry — per-handler invocation backstop; a hung adapter "
        "call raises TimeoutError (composes with retry) instead of wedging the poll "
        "loop. BEHAVIOR CHANGE: omitting timeout now auto-defaults to the poll "
        "interval — pass timeout=None to disable "
        "(see: cosalette ai help resilience, ADR-024).",
    ],
    "0.4.0": [
        "@app.react — domain-event reactors for state objects: reactor fires at "
        "execution boundaries when state has pending events "
        "(see: cosalette ai help react)",
        "BREAKING: @app.device handlers must be async generators (add yield after "
        "each unit of work); plain coroutines now raise TypeError",
        "yield in @app.device is the reaction boundary — reactors fire here "
        "before the next ctx.sleep()",
        "Typed handler contracts — Pydantic v2 TypeAdapter validates/serializes "
        "payload and return annotations at runtime; Depends, Payload, Topic, Message "
        "markers; raw str escape hatch (see: cosalette ai help contracts)",
        "Router — composition primitive for multi-module apps: prefix, tags, "
        "topic prefixing, scoped adapters (see: cosalette ai help router)",
        "AsyncAPI manifest inspection — cosalette manifest produces JSON/table "
        "with contract metadata for code generators and doc tooling "
        "(see: cosalette ai help manifest)",
    ],
    "0.6.0": [
        "store= now optional — when omitted, the framework auto-resolves a default "
        "JsonFileStore from the app name (<NAME>_STORE_PATH env, else "
        "$XDG_STATE_HOME/<name>/store.json, else ~/.local/state/<name>/store.json), "
        "so ADR-048 orphaned retained-topic cleanup works with zero config. Pass "
        "store=None to opt out; pass an explicit Store/factory to override "
        "(see: cosalette ai help persistence, ADR-049).",
        "set_default_store_backend() — process-wide override of the auto-resolved "
        "default store backend (e.g. SqliteStore); call once at startup before "
        "constructing any App; pass None to reset to JsonFileStore "
        "(see: cosalette ai help persistence, ADR-049).",
        "Ephemeral default-store startup WARNING — when the auto-resolved default "
        "store is ephemeral in a container, no <NAME>_STORE_PATH is set, and the "
        "app's entity set may vary by config (callable name=/enabled= or "
        "@app.on_configure hooks), the framework emits a WARNING at bootstrap. "
        "Apps with a fixed static entity set are exempt. "
        "(see: cosalette ai help persistence, ADR-049).",
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


@functools.lru_cache(maxsize=1)
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
   • @app.device handlers are async generators — yield marks the reaction boundary
   • @app.react() for domain-event reactors: state stays pure, I/O lives in reactors
   • name=callable for multi-device registration from settings
   • Type-based dependency injection + init= factories
   • Persistent state via DeviceContext.state + callable store factories

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
   cosalette ai help sub-entities   — Sub-component lifecycle management
   cosalette ai help triggerable    — On-demand MQTT-triggered telemetry
   cosalette ai help multi-device   — Declarative multi-device registration
   cosalette ai help contracts      — Contract metadata on registrations
   cosalette ai help manifest       — Inspect app registration surface
   cosalette ai help react          — Domain-event reactors + async-generator
                                      device semantics
   cosalette ai help availability   — Transport availability signaling
   cosalette ai help persistence     — Store backends, default resolution,
                                      persist= policies"""


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
        except InvalidVersion:
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
