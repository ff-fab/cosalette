"""AI init command helpers: template copy, agent file management, pointer blocks."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from cosalette._package_cli._json_config import (
    _manage_kilo_config,
    _manage_opencode_config,
)
from cosalette._package_cli._utils import (
    _find_repo_root,
    _get_canonical_relative_path,
    _is_canonical_default_target,
)


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
        typer.echo("   Possible packaging issue or bad dev setup.")
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


def _handle_agent_file_management(
    target: Path,
    *,
    opencode: bool = False,
    kilo: bool = False,
) -> None:
    """Manage AGENTS.md and CLAUDE.md pointer blocks for canonical installs.

    Args:
        target: Target path for the instruction file
        opencode: When True, also create/update opencode.json.
        kilo: When True, also create/update kilo.jsonc.
    """
    if not _is_canonical_default_target(target):
        typer.echo(
            "📝 Custom target path — skipping AGENTS.md/CLAUDE.md auto-management"
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

    # Opt-in: opencode.ai and kilo.ai config files
    if opencode:
        _manage_opencode_config(canonical_path, repo_root)
    if kilo:
        _manage_kilo_config(canonical_path, repo_root)


def _display_next_steps(target: Path) -> None:
    """Display appropriate next steps based on target type.

    Args:
        target: Target path for the instruction file
    """
    typer.echo()
    if _is_canonical_default_target(target):
        typer.echo("Next steps:")
        typer.echo("  • Customize instruction file for your project")
        typer.echo("  • Run 'cosalette ai prime' for framework overview and patterns")
        typer.echo("  • Run 'cosalette ai help <topic>' for topic-specific guidance")
    else:
        typer.echo("Next steps:")
        typer.echo(
            "  • Add framework guidance to AGENTS.md/CLAUDE.md manually if needed"
        )
        typer.echo("  • Run 'cosalette ai prime' for framework overview + patterns")
        typer.echo("  • Run 'cosalette ai help <topic>' for topic guidance")


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
        typer.echo(f"❗️  Skipping {file_path.name}: symlink detected")
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
