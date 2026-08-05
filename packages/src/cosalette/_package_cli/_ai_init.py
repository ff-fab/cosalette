"""AI init command helpers: template copy, agent file management, pointer blocks."""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

import typer

from cosalette._package_cli._json_config import (
    _manage_claude_config,
    _manage_kilo_config,
    _manage_kilo_mcp_config,
    _manage_opencode_config,
)
from cosalette._package_cli._utils import (
    _atomic_write_text,
    _find_repo_root,
    _get_canonical_relative_path,
    _is_canonical_default_target,
)

_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):")
_FM_OWNED_KEYS = frozenset({"description", "applyTo"})


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_text, body) or (None, original_text) if no frontmatter."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    return None, text


def _parse_frontmatter_blocks(fm_text: str) -> list[tuple[str | None, str]]:
    """Parse frontmatter text into (key_name_or_None, block_text) pairs.

    Returns one entry per top-level key (plus an optional leading preamble entry
    with key=None for blank/comment lines before the first key).
    """
    lines = fm_text.splitlines(keepends=True)
    blocks: list[tuple[str | None, str]] = []
    current_key: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _TOP_LEVEL_KEY.match(line)
        if m:
            if current_lines:
                blocks.append((current_key, "".join(current_lines)))
            current_key = m.group(1)
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        blocks.append((current_key, "".join(current_lines)))

    return blocks


def _template_key_index(
    template_blocks: list[tuple[str | None, str]],
) -> dict[str, str]:
    """Build {key: text} index for non-None template keys."""
    result: dict[str, str] = {}
    for key, text in template_blocks:
        if key is not None:
            result[key] = text
    return result


def _merge_frontmatter_blocks(
    target_blocks: list[tuple[str | None, str]],
    template_blocks: list[tuple[str | None, str]],
) -> list[str]:
    """Merge template-owned keys into target blocks, preserving all others."""
    template_key_text = _template_key_index(template_blocks)
    merged: list[str] = []
    seen_owned: set[str] = set()
    for key, text in target_blocks:
        if key in _FM_OWNED_KEYS:
            if key in template_key_text:
                merged.append(template_key_text[key])
            seen_owned.add(key)
        else:
            merged.append(text)
    for key, text in template_blocks:
        if key in _FM_OWNED_KEYS and key not in seen_owned:
            merged.append(text)
    return merged


def _merge_instruction_content(target_text: str, template_text: str) -> str:
    """Merge template-owned frontmatter keys into target, preserving downstream keys.

    The template owns the keys listed in ``_FM_OWNED_KEYS``. Every other top-level
    key in the target frontmatter is preserved verbatim. The body is always replaced
    by the template body. If the target has no parseable frontmatter, returns
    *template_text* unchanged.
    """
    target_fm, _ = _split_frontmatter(target_text)
    template_fm, template_body = _split_frontmatter(template_text)
    if target_fm is None or template_fm is None:
        return template_text
    merged = _merge_frontmatter_blocks(
        _parse_frontmatter_blocks(target_fm),
        _parse_frontmatter_blocks(template_fm),
    )
    return "---\n" + "".join(merged) + "---\n" + template_body


def _check_instructions(target: Path, template_path: Path) -> int:
    """Check whether *target* is up to date with *template_path*.

    Returns 0 if up to date, 1 if missing or stale.
    """
    if not template_path.exists():
        typer.echo(f"❌ Template not found: {template_path}")
        typer.echo("   Possible packaging issue or bad dev setup.")
        return 1

    if not target.exists():
        typer.echo(f"❌ Instruction file missing: {target}")
        typer.echo("   Run 'cosalette ai init' to install.")
        return 1

    current_text = target.read_text()
    expected_text = _merge_instruction_content(current_text, template_path.read_text())

    if current_text == expected_text:
        typer.echo(f"✅ cosalette instructions are up to date: {target}")
        return 0

    typer.echo(f"❌ cosalette instructions are out of date: {target}")
    diff_lines = difflib.unified_diff(
        current_text.splitlines(keepends=True),
        expected_text.splitlines(keepends=True),
        fromfile="current",
        tofile="expected",
    )
    typer.echo("".join(diff_lines), nl=False)
    return 1


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
        is_refresh = target.exists()
        if is_refresh:
            content = _merge_instruction_content(
                target.read_text(), template_path.read_text()
            )
        else:
            content = template_path.read_text()
        _atomic_write_text(target, content)

        status = "✅ Refreshed" if is_refresh else "✅ Installed"
        typer.echo(f"{status} cosalette instructions: {target}")

        return is_refresh
    except Exception as e:
        typer.echo(f"❌ Failed to install instruction file: {e}")
        raise typer.Exit(1) from e


def _claude_imports_agents(claude_path: Path) -> bool:
    """Return True when *claude_path* already delegates to AGENTS.md.

    Symlink-safe (CWE-59): reads the symlink target via ``os.readlink`` rather
    than following it, and guards every I/O call with try/except OSError.
    """
    try:
        if claude_path.is_symlink():
            try:
                target = os.readlink(claude_path)
            except OSError:
                return False
            return Path(target).name == "AGENTS.md"
        if claude_path.exists():
            try:
                text = claude_path.read_text()
            except OSError:
                return False
            return any(
                (s := line.strip()).startswith("@") and s.endswith("AGENTS.md")
                for line in text.splitlines()
            )
    except OSError:
        return False
    return False


def _handle_claude_pointer(claude_path: Path, canonical_path: str) -> None:
    """Update or skip CLAUDE.md pointer block."""
    if _claude_imports_agents(claude_path):
        typer.echo(
            "ℹ️  CLAUDE.md already imports AGENTS.md "
            "— skipping duplicate cosalette pointer block"
        )
        return
    claude_updated = _manage_agent_pointer_block(claude_path, canonical_path)
    if claude_updated:
        typer.echo("✅ Updated CLAUDE.md pointer block")
    elif claude_path.is_symlink():
        pass  # callee already printed the symlink-skip message
    elif claude_path.exists():
        typer.echo("ℹ️  CLAUDE.md exists but no updates needed")


def _handle_agent_file_management(
    target: Path,
    *,
    opencode: bool = False,
    kilo: bool = False,
    claude: bool = False,
) -> None:
    """Manage AGENTS.md and CLAUDE.md pointer blocks for canonical installs.

    Args:
        target: Target path for the instruction file
        opencode: When True, also create/update opencode.json.
        kilo: When True, also create/update kilo.jsonc (including its MCP entry).
        claude: When True, also create/update .mcp.json for Claude Code.
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
    if agents_updated:
        typer.echo("✅ Updated AGENTS.md pointer block")

    _handle_claude_pointer(claude_path, canonical_path)

    # Opt-in: opencode.ai and kilo.ai config files
    if opencode:
        typer.echo(
            "⚠️  --opencode is deprecated; prefer --kilo (Kilo supersedes OpenCode)."
        )
        _manage_opencode_config(canonical_path, repo_root)
    if kilo:
        _manage_kilo_config(canonical_path, repo_root)
        _manage_kilo_mcp_config(repo_root)
    if claude:
        _manage_claude_config(repo_root)


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


def _compute_agent_block_content(
    current_content: str,
    content_block: str,
    marker_begin: str,
    marker_end: str,
) -> str | None:
    """Compute updated file content with the managed block inserted or replaced.

    Returns the new content string, or ``None`` when the file is already
    up to date (no write needed).
    """
    begin_idx = current_content.find(marker_begin)
    end_idx = current_content.find(marker_end)
    if begin_idx != -1 and end_idx != -1:
        end_idx = end_idx + len(marker_end)
        new_content = (
            current_content[:begin_idx] + content_block + current_content[end_idx:]
        )
    else:
        if current_content.strip():
            new_content = current_content + f"\n\n{content_block}\n"
        else:
            new_content = content_block + "\n"
    return new_content if new_content != current_content else None


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
        if file_path.name == "AGENTS.md":
            initial_content = f"""# Agent Instructions

{content_block}
"""
            _atomic_write_text(file_path, initial_content)
            return True
        return False

    current_content = file_path.read_text()
    new_content = _compute_agent_block_content(
        current_content, content_block, marker_begin, marker_end
    )
    if new_content is None:
        return False
    _atomic_write_text(file_path, new_content)
    return True
