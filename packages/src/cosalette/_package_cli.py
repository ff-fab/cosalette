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

# Create MCP command group (lazy — only imports fastmcp when invoked)
mcp_app = typer.Typer(help="MCP server commands")
ai_app.add_typer(mcp_app, name="mcp")


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


def _find_repo_root() -> Path:
    """Walk up from cwd to find the repository root (.git marker).

    Falls back to cwd if no .git directory or file is found.
    """
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def _find_instructions_dir() -> Path:
    """Return the canonical instructions directory relative to the repo root."""
    return _find_repo_root() / ".github" / "instructions"


def _get_canonical_relative_path(target: Path) -> str:
    """Get a robust relative path to the target from the repo root.

    Falls back to absolute path if relative calculation fails.
    """
    try:
        return str(target.resolve().relative_to(_find_repo_root().resolve()))
    except ValueError:
        return str(target.resolve())


def _is_canonical_default_target(target: Path) -> bool:
    """Check if the target is the canonical default instructions file.

    Returns True only for .github/instructions/cosalette.instructions.md
    """
    try:
        target_resolved = target.resolve()
        canonical_default = (
            _find_repo_root() / ".github" / "instructions" / "cosalette.instructions.md"
        ).resolve()
        return target_resolved == canonical_default
    except OSError:
        # If path resolution fails, be conservative and return False
        return False


def _manage_mcp_config() -> None:
    """Create or update .vscode/mcp.json if cosalette[mcp] is installed."""
    try:
        import fastmcp  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return  # MCP not installed, skip

    repo_root = _find_repo_root()
    vscode_dir = repo_root / ".vscode"
    mcp_config = vscode_dir / "mcp.json"

    config = {
        "servers": {
            "cosalette": {
                "command": "cosalette",
                "args": ["ai", "mcp", "serve"],
                "env": {},
            }
        }
    }

    # If file exists, merge (don't overwrite other servers)
    if mcp_config.exists():
        import json

        try:
            existing = json.loads(mcp_config.read_text())
            cos_cfg = config["servers"]["cosalette"]
            if existing.get("servers", {}).get("cosalette") == cos_cfg:
                return  # Already configured correctly
            existing.setdefault("servers", {})["cosalette"] = cos_cfg
            config = existing
        except json.JSONDecodeError, KeyError:
            # If existing file is malformed, overwrite with our config
            pass

    vscode_dir.mkdir(parents=True, exist_ok=True)
    import json

    mcp_config.write_text(json.dumps(config, indent=2) + "\n")
    typer.echo("✅ Configured .vscode/mcp.json for cosalette MCP server")


def _copy_template_to_target(template_path: Path, target: Path) -> bool:
    """Copy template file to target location and return whether it was a refresh.

    Args:
        template_path: Path to the template file
        target: Target path for the instruction file

    Returns:
        True if this was a refresh (file existed), False if new install

    Raises:
        typer.Exit: If template doesn't exist or copy operation fails
    """
    if not template_path.exists():
        typer.echo(f"❌ Template not found: {template_path}")
        typer.echo(
            "   This may indicate a packaging issue or development setup problem."
        )
        raise typer.Exit(1)

    try:
        # Check if this is a refresh (target exists before copy)
        is_refresh = target.exists()
        shutil.copy2(template_path, target)

        status = "✅ Refreshed" if is_refresh else "✅ Installed"
        typer.echo(f"{status} cosalette instructions: {target}")

        return is_refresh
    except Exception as e:
        typer.echo(f"❌ Failed to install instruction file: {e}")
        raise typer.Exit(1) from e


def _handle_agent_file_management(target: Path) -> None:
    """Manage AGENTS.md and CLAUDE.md pointer blocks for canonical installs.

    Args:
        target: Target path for the instruction file
    """
    if not _is_canonical_default_target(target):
        typer.echo(
            "📝 Custom target path - skipping AGENTS.md/CLAUDE.md auto-management"
        )
        return

    # Get robust relative path to canonical instructions file
    canonical_path = _get_canonical_relative_path(target)

    # Manage agent pointer blocks (resolved from repo root)
    repo_root = _find_repo_root()
    agents_path = repo_root / "AGENTS.md"
    claude_path = repo_root / "CLAUDE.md"
    agents_updated = _manage_agent_pointer_block(agents_path, canonical_path)
    claude_updated = _manage_agent_pointer_block(claude_path, canonical_path)

    # Report pointer block updates
    if agents_updated:
        typer.echo("✅ Updated AGENTS.md pointer block")
    if claude_updated:
        typer.echo("✅ Updated CLAUDE.md pointer block")
    elif claude_path.exists():
        typer.echo("ℹ️  CLAUDE.md exists but no updates needed")


def _display_next_steps(target: Path) -> None:
    """Display appropriate next steps based on target type.

    Args:
        target: Target path for the instruction file
    """
    typer.echo()
    if _is_canonical_default_target(target):
        typer.echo("Next steps:")
        typer.echo(
            "  • Customize the instruction file for your project's specific needs"
        )
        typer.echo("  • Run 'cosalette ai prime' for framework overview and patterns")
        typer.echo("  • Run 'cosalette ai help <topic>' for topic-specific guidance")
    else:
        typer.echo("Next steps:")
        typer.echo(
            "  • Add framework guidance to your AGENTS.md/CLAUDE.md manually if needed"
        )
        typer.echo("  • Run 'cosalette ai prime' for framework overview and patterns")
        typer.echo("  • Run 'cosalette ai help <topic>' for topic-specific guidance")


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

    # Safety: refuse to follow symlinks (CWE-59)
    if file_path.is_symlink():
        typer.echo(f"⚠️  Skipping {file_path.name}: symlink detected")
        return False

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

    # Get the template content and copy to target
    assets_dir = _get_package_assets_dir()
    template_path = assets_dir / "cosalette.instructions.md"

    _copy_template_to_target(template_path, target)
    _handle_agent_file_management(target)
    _manage_mcp_config()
    _display_next_steps(target)


@ai_app.command("prime")
def ai_prime() -> None:
    """Print concise downstream agent/developer bootstrap summary."""
    from cosalette._ai_content import get_prime_content

    typer.echo(get_prime_content())


@ai_app.command("help")
def ai_help(
    topic: Annotated[
        str,
        typer.Argument(
            help="Help topic (telemetry, testing, configuration, architecture)"
        ),
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
# MCP Commands
# ---------------------------------------------------------------------------


@mcp_app.command("serve")
def mcp_serve(
    transport: Annotated[
        str, typer.Option("--transport", "-t", help="Transport type (stdio or sse)")
    ] = "stdio",
    port: Annotated[
        int, typer.Option("--port", "-p", help="Port for SSE transport")
    ] = 8080,
) -> None:
    """Start the cosalette MCP server."""
    try:
        import fastmcp  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        typer.echo("❌ MCP support not installed. Run: uv add 'cosalette[mcp]'")
        raise typer.Exit(1) from None

    from cosalette._mcp import create_server

    server = create_server()
    if transport == "sse":
        server.run(transport="sse", port=port)
    else:
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
    from cosalette._ai_content import get_help_content

    typer.echo(get_help_content("telemetry"))


def _get_testing_help() -> None:
    """Print testing development guidance."""
    from cosalette._ai_content import get_help_content

    typer.echo(get_help_content("testing"))


def _get_configuration_help() -> None:
    """Print configuration development guidance."""
    from cosalette._ai_content import get_help_content

    typer.echo(get_help_content("configuration"))


def _get_architecture_help() -> None:
    """Print architectural patterns and design rationale."""
    from cosalette._ai_content import get_help_content

    typer.echo(get_help_content("architecture"))


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
