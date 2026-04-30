"""JSON/JSONC parsing utilities and config file management."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

import typer

from cosalette._package_cli._utils import _find_repo_root


def _skip_json_string(text: str, i: int) -> tuple[list[str], int]:
    """Consume a JSON string starting at position i (on the opening quote).

    Returns the accumulated characters (including delimiters) and the new
    position (one past the closing quote).
    """
    result = ['"']
    i += 1  # move past opening quote
    escaping = False
    while i < len(text):
        char = text[i]
        result.append(char)
        if escaping:
            escaping = False
        elif char == "\\":
            escaping = True
        elif char == '"':
            return result, i + 1
        i += 1
    return result, i  # unterminated string — return what we have


def _strip_jsonc_comments(text: str) -> str:
    """Strip // line comments and /* */ block comments from JSONC text.

    Uses a character-level scanner that delegates string scanning to
    ``_skip_json_string``, so comment markers inside string values (e.g.
    URLs, descriptions) are preserved verbatim.
    """
    result: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if char == '"':
            chars, i = _skip_json_string(text, i)
            result.extend(chars)
        elif char == "/" and next_char == "/":
            # Line comment — skip to end of line
            end = text.find("\n", i)
            if end == -1:
                break
            result.append("\n")
            i = end + 1
        elif char == "/" and next_char == "*":
            # Block comment — skip to */
            end = text.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
        else:
            result.append(char)
            i += 1
    return "".join(result)


def _load_existing_config(
    config_path: Path, filename: str, strip_comments: bool
) -> dict[str, object] | None:
    """Parse an existing JSON/JSONC config file.

    Returns the parsed dict, or ``None`` when the file should be skipped
    (malformed content or non-object root).  Emits a user-facing warning
    in both skip cases.
    """
    try:
        raw = config_path.read_text()
        if strip_comments:
            raw = _strip_jsonc_comments(raw)
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        typer.echo(
            f"\u2757\ufe0f  Skipping {filename}: file contains malformed JSON; "
            "fix the file manually to preserve existing settings"
        )
        return None

    if not isinstance(parsed, dict):
        typer.echo(
            f"\u2757\ufe0f  Skipping {filename}: top-level JSON value must be an "
            "object; fix the file manually to preserve existing settings"
        )
        return None

    return parsed


def _mcp_paths_are_safe(vscode_dir: Path, mcp_config: Path) -> bool:
    """Return True when neither the .vscode dir nor mcp.json is a symlink."""
    return not (
        vscode_dir.is_symlink() or (mcp_config.exists() and mcp_config.is_symlink())
    )


def _merge_mcp_server_config(
    mcp_config: Path,
    cos_cfg: dict[str, object],
    fallback_config: dict[str, object],
) -> dict[str, object] | None:
    """Load and merge the existing mcp.json with cosalette's entry.

    Returns the merged config dict, or ``None`` when no write is needed
    (already configured correctly).  Returns *fallback_config* on parse
    errors so the caller can overwrite a malformed file.
    """
    try:
        existing = json.loads(mcp_config.read_text())
        if not isinstance(existing, dict):
            existing = {}
        if existing.get("servers", {}).get("cosalette") == cos_cfg:
            return None
        existing.setdefault("servers", {})["cosalette"] = cos_cfg
        return existing
    except json.JSONDecodeError, KeyError:
        return fallback_config


def _manage_mcp_config() -> None:
    """Create or update .vscode/mcp.json if cosalette[mcp] is installed."""
    try:
        import fastmcp  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return  # MCP not installed, skip

    repo_root = _find_repo_root()
    vscode_dir = repo_root / ".vscode"
    mcp_config = vscode_dir / "mcp.json"

    cos_cfg: dict[str, object] = {
        "command": sys.executable,
        "args": ["-m", "cosalette", "ai", "mcp", "serve"],
        "env": {},
    }
    config: dict[str, object] = {"servers": {"cosalette": cos_cfg}}

    # Safety: refuse to follow symlinks (CWE-59)
    if not _mcp_paths_are_safe(vscode_dir, mcp_config):
        typer.echo("❗️  Skipping MCP config: symlink detected in .vscode/mcp.json path")
        return

    if mcp_config.exists():
        merged = _merge_mcp_server_config(mcp_config, cos_cfg, config)
        if merged is None:
            return  # Already configured correctly
        config = merged

    vscode_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps(config, indent=2) + "\n"
    # Atomic write: write to a sibling temp file, then os.replace() (CWE-59)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=vscode_dir, prefix=".mcp.json.tmp")
    try:
        os.write(tmp_fd, content.encode())
        os.close(tmp_fd)
        os.replace(tmp_path, mcp_config)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    typer.echo("✅ Configured .vscode/mcp.json for cosalette MCP server")


def _manage_json_config(
    canonical_path: str,
    repo_root: Path,
    filename: str,
    *,
    schema_seed: dict[str, object] | None = None,
    strip_comments: bool = False,
) -> None:
    """Create or update a JSON/JSONC config file to include cosalette instructions.

    Idempotent, symlink-safe (CWE-59), and fail-closed: a file that cannot be
    parsed is skipped with a warning rather than overwritten.

    Args:
        canonical_path: Relative path to the instruction file.
        repo_root: Repository root directory.
        filename: Config file name (e.g. "opencode.json" or "kilo.jsonc").
        schema_seed: Initial dict to seed when creating a new file.
        strip_comments: When True, strip JSONC comments before parsing.
    """
    config_path = repo_root / filename

    # Safety: refuse to follow symlinks (CWE-59)
    if config_path.is_symlink():
        typer.echo(f"\u2757\ufe0f  Skipping {filename}: symlink detected")
        return

    if config_path.exists():
        existing = _load_existing_config(config_path, filename, strip_comments)
        if existing is None:
            return
    else:
        existing = dict(schema_seed) if schema_seed else {}

    raw_instructions = existing.get("instructions")
    instructions: list[str] = (
        [x for x in raw_instructions if isinstance(x, str)]
        if isinstance(raw_instructions, list)
        else []
    )

    if canonical_path in instructions:
        return  # Already configured

    instructions.append(canonical_path)
    existing["instructions"] = instructions
    content = json.dumps(existing, indent=2) + "\n"

    # Atomic write: write to a sibling temp file, then os.replace().
    # os.replace() (rename(2) on POSIX) replaces the destination path itself,
    # so even if a symlink is raced in after our is_symlink() check, the
    # symlink is replaced rather than followed (CWE-59 hardening).
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=config_path.parent, prefix=f".{filename}.tmp"
    )
    try:
        os.write(tmp_fd, content.encode())
        os.close(tmp_fd)
        os.replace(tmp_path, config_path)
    except Exception:
        # Clean up temp file on any failure, then re-raise
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    typer.echo(f"\u2705 Configured {filename} for cosalette instructions")


def _manage_opencode_config(canonical_path: str, repo_root: Path) -> None:
    """Create or update opencode.json to include cosalette instructions."""
    _manage_json_config(
        canonical_path,
        repo_root,
        "opencode.json",
        schema_seed={"$schema": "https://opencode.ai/config.json"},
    )


def _manage_kilo_config(canonical_path: str, repo_root: Path) -> None:
    """Create or update kilo.jsonc to include cosalette instructions."""
    _manage_json_config(
        canonical_path,
        repo_root,
        "kilo.jsonc",
        strip_comments=True,
    )
