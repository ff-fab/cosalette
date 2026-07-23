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

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from cosalette._package_cli._ai_init import (
    _copy_template_to_target,
    _display_next_steps,
    _handle_agent_file_management,
    _manage_agent_pointer_block,
)
from cosalette._package_cli._json_config import (
    _manage_kilo_config,
    _manage_mcp_config,
    _manage_opencode_config,
    _strip_jsonc_comments,
)
from cosalette._package_cli._utils import (
    _find_instructions_dir,
    _get_canonical_relative_path,
    _get_package_assets_dir,
    _get_version,
    _is_canonical_default_target,
)
from cosalette._schema._cli import schema_app

# ---------------------------------------------------------------------------
# Main CLI app
# ---------------------------------------------------------------------------

app = typer.Typer(help="cosalette — IoT-to-MQTT framework CLI")

# Create AI command group
ai_app = typer.Typer(help="AI agent commands for cosalette development")
app.add_typer(ai_app, name="ai")

# Create schema command group
# (lazy — schema subpackage deps imported on first command invocation)
app.add_typer(schema_app, name="schema")

# Create MCP command group (lazy — only imports fastmcp when invoked)
mcp_app = typer.Typer(help="MCP server commands")
ai_app.add_typer(mcp_app, name="mcp")


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
    opencode: Annotated[
        bool,
        typer.Option(
            "--opencode",
            help="Create/update opencode.json with the instruction file path",
        ),
    ] = False,
    kilo: Annotated[
        bool,
        typer.Option(
            "--kilo",
            help="Create/update kilo.jsonc with the instruction file path",
        ),
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
        typer.echo("   Use --force to overwrite, or specify different --target")
        raise typer.Exit(1)

    # Get the template content and copy to target
    assets_dir = _get_package_assets_dir()
    template_path = assets_dir / "cosalette.instructions.md"

    _copy_template_to_target(template_path, target)
    _handle_agent_file_management(target, opencode=opencode, kilo=kilo)
    _manage_mcp_config()
    _display_next_steps(target)


@ai_app.command("prime")
def ai_prime(
    upgrade_from: Annotated[
        str | None,
        typer.Option(
            "--upgrade-from",
            help="Show what's new since this version (e.g., --upgrade-from=0.2.1)",
        ),
    ] = None,
) -> None:
    """Print concise downstream agent/developer bootstrap summary."""
    from cosalette._ai_content import get_prime_content

    content = get_prime_content()
    if upgrade_from:
        from cosalette._ai_content import get_whats_new_content

        whats_new = get_whats_new_content(upgrade_from)
        if whats_new:
            content += "\n\n" + whats_new

    typer.echo(content)


@ai_app.command("help")
def ai_help(
    topic: Annotated[
        str,
        typer.Argument(help="Help topic (e.g. telemetry, commands, health, …)"),
    ],
) -> None:
    """Print curated topic help for downstream app development."""
    from cosalette._ai_content import AVAILABLE_TOPICS, get_help_content

    if topic not in AVAILABLE_TOPICS:
        available = ", ".join(AVAILABLE_TOPICS)
        typer.echo(f"❌ Unknown topic: {topic}")
        typer.echo(f"   Available: {available}")
        raise typer.Exit(1)

    typer.echo(get_help_content(topic))


# ---------------------------------------------------------------------------
# MCP Commands
# ---------------------------------------------------------------------------


@mcp_app.command("serve")
def mcp_serve(
    transport: Annotated[
        str,
        typer.Option(
            "--transport",
            "-t",
            help="Transport type (stdio only; SSE is intentionally unsupported)",
        ),
    ] = "stdio",
) -> None:
    """Start the cosalette MCP server."""
    if transport != "stdio":
        typer.echo(
            "❌ cosalette MCP only supports stdio transport. "
            "SSE is not exposed because MCP tools import local application code."
        )
        raise typer.Exit(1)

    try:
        import fastmcp  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        typer.echo("❌ MCP support not installed. Run: uv add 'cosalette[mcp]'")
        raise typer.Exit(1) from None

    from cosalette._mcp import create_server

    server = create_server()
    server.run(transport="stdio")


@app.command("init", hidden=True)
def init_alias(
    target: Annotated[
        Path | None,
        typer.Option("--target", "-t", help="Target path for instruction file"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite existing instruction file")
    ] = False,
    opencode: Annotated[
        bool,
        typer.Option("--opencode", help="Create/update opencode.json"),
    ] = False,
    kilo: Annotated[
        bool,
        typer.Option("--kilo", help="Create/update kilo.jsonc"),
    ] = False,
) -> None:
    """Alias for 'cosalette ai init'."""
    ai_init(target=target, force=force, opencode=opencode, kilo=kilo)


@app.command("prime", hidden=True)
def prime_alias() -> None:
    """Alias for 'cosalette ai prime'."""
    ai_prime()


# ---------------------------------------------------------------------------
# Version and info
# ---------------------------------------------------------------------------


@app.command("manifest")
def manifest_cmd(
    app_spec: Annotated[
        str,
        typer.Argument(
            help="App specification in format 'module.path:attribute' "
            "(e.g. 'myapp.main:app')"
        ),
    ],
    table: Annotated[
        bool,
        typer.Option("--table", help="Output as human-readable table instead of JSON"),
    ] = False,
) -> None:
    """Print the cosalette app registry manifest as JSON or a human-readable table.

    Imports the specified module to inspect its registrations, so the module's
    top-level code runs at import time (like 'uvicorn module:app'). Do not run
    against a module or repository you do not trust — see SECURITY.md.
    """
    from cosalette._app import App
    from cosalette._mcp._imports import _import_from_spec_unchecked
    from cosalette._mcp._introspect import format_asyncapi_table

    # Developer-invoked CLI: the ``module:app`` spec is a documented trust
    # boundary (see SECURITY.md), like uvicorn/gunicorn — not a remotely
    # reachable input, so it is not subject to the MCP import allowlist.
    obj, err = _import_from_spec_unchecked(app_spec)
    if err is not None:
        typer.echo(err)
        raise typer.Exit(1)

    if not isinstance(obj, App):
        actual_type = type(obj).__name__
        typer.echo(f"❌ '{app_spec}' is not an App instance (found {actual_type})")
        raise typer.Exit(1)

    if table:
        typer.echo(format_asyncapi_table(obj.asyncapi()))
    else:
        typer.echo(json.dumps(obj.asyncapi(), indent=2))


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
# Console script entry point
# ---------------------------------------------------------------------------


def main_cli() -> None:
    """Entry point for the cosalette console script."""
    try:
        app()
    except KeyboardInterrupt:
        typer.echo("\n❌ Interrupted", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main_cli()
