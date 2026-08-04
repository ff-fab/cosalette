"""JSON/JSONC parsing utilities and config file management."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import typer

from cosalette._package_cli._utils import _atomic_write_text, _find_repo_root


class _SurgicalFail:
    """Sentinel returned when surgical JSONC insertion cannot be done safely."""


_SURGICAL_FAIL = _SurgicalFail()  # singleton sentinel for fail-closed returns


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


def _skip_comment_at(text: str, i: int) -> int | None:
    """If a // or /* */ comment starts at i, return the position after it; else None."""
    if i + 1 < len(text) and text[i : i + 2] == "//":
        end = text.find("\n", i)
        return (end + 1) if end != -1 else len(text)
    if i + 1 < len(text) and text[i : i + 2] == "/*":
        end = text.find("*/", i + 2)
        return (end + 2) if end != -1 else len(text)
    return None


def _skip_ws_comments(text: str, i: int) -> int:
    """Skip whitespace and JSONC comments from position i."""
    while i < len(text):
        if text[i] in " \t\r\n":
            i += 1
        elif (new_i := _skip_comment_at(text, i)) is not None:
            i = new_i
        else:
            break
    return i


def _skip_balanced_jsonc(text: str, i: int) -> int:
    """Skip balanced {} or [] starting at i; return position after closing bracket."""
    depth = 1
    i += 1
    while i < len(text) and depth > 0:
        c = text[i]
        if c == '"':
            _, i = _skip_json_string(text, i)
        elif (new_i := _skip_comment_at(text, i)) is not None:
            i = new_i
        elif c in ("{", "["):
            depth += 1
            i += 1
        elif c in ("}", "]"):
            depth -= 1
            i += 1
        else:
            i += 1
    return i


def _skip_jsonc_value(text: str, i: int) -> int:
    """Skip a single JSONC value at position i; return position after it."""
    c = text[i] if i < len(text) else ""
    if c == '"':
        _, i = _skip_json_string(text, i)
    elif c in ("{", "["):
        i = _skip_balanced_jsonc(text, i)
    else:
        _STOP = (",", "}", "]", "\n", "\r", " ", "\t", "/")
        while i < len(text) and text[i] not in _STOP:
            i += 1
    return i


def _parse_jsonc_root(raw: str) -> dict[str, object] | _SurgicalFail:
    """Strip comments, parse JSON, and verify the root is a dict."""
    try:
        parsed = json.loads(_strip_jsonc_comments(raw))
    except json.JSONDecodeError:
        return _SURGICAL_FAIL
    if not isinstance(parsed, dict):
        return _SURGICAL_FAIL
    return parsed


def _find_root_brace(raw: str) -> int | _SurgicalFail:
    """Return the index of the opening '{' of the root object."""
    i = _skip_ws_comments(raw, 0)
    if i >= len(raw) or raw[i] != "{":
        return _SURGICAL_FAIL
    return i


def _scan_root_key(raw: str, i: int) -> tuple[str, int] | _SurgicalFail:
    """Parse a JSON key at position i; return (key, position_at_value_start)."""
    key_chars, i = _skip_json_string(raw, i)
    try:
        key = json.loads("".join(key_chars))
    except json.JSONDecodeError:
        return _SURGICAL_FAIL
    i = _skip_ws_comments(raw, i)
    if i >= len(raw) or raw[i] != ":":
        return _SURGICAL_FAIL
    i += 1
    return key, _skip_ws_comments(raw, i)


def _check_instructions_value(raw: str, i: int) -> tuple[int, int] | _SurgicalFail:
    """Verify value at i is '['; return (array_start, array_end) or _SURGICAL_FAIL."""
    if i >= len(raw) or raw[i] != "[":
        return _SURGICAL_FAIL
    return i, _skip_balanced_jsonc(raw, i)


def _locate_instructions_array(
    raw: str, brace_pos: int
) -> tuple[int, int] | None | _SurgicalFail:
    """Scan the root object for the 'instructions' array.

    Returns (array_start, array_end), None if the key is absent, or
    _SURGICAL_FAIL if the structure cannot be parsed safely.
    """
    i = brace_pos + 1
    while i < len(raw):
        i = _skip_ws_comments(raw, i)
        if i >= len(raw):
            return _SURGICAL_FAIL
        c = raw[i]
        if c == "}":
            return None
        if c == ",":
            i += 1
            continue
        if c != '"':
            return _SURGICAL_FAIL
        kv = _scan_root_key(raw, i)
        if isinstance(kv, _SurgicalFail):
            return _SURGICAL_FAIL
        key, i = kv
        if key == "instructions":
            return _check_instructions_value(raw, i)
        i = _skip_jsonc_value(raw, i)
    return _SURGICAL_FAIL


def _insert_new_instructions_member(raw: str, brace_pos: int, element: str) -> str:
    """Insert a new "instructions" member right after the opening '{'."""
    insert_pos = brace_pos + 1
    peek = _skip_ws_comments(raw, insert_pos)
    indent = "  "
    if peek < len(raw) and raw[peek] == "}":
        new_member = f'\n{indent}"instructions": [{element}]\n'
    else:
        new_member = f'\n{indent}"instructions": [{element}],'
    return raw[:insert_pos] + new_member + raw[insert_pos:]


def _append_into_empty_array(
    raw: str, array_start: int, close_pos: int, element: str
) -> str:
    """Insert element into an empty or comment-only array (no existing values)."""
    last_nl = raw.rfind("\n", array_start, close_pos)
    if last_nl == -1:
        # Inline [] on a single line — just expand it
        return raw[: array_start + 1] + f"\n  {element}\n" + raw[close_pos:]
    # Multi-line array: determine indent from the ] line, insert just before it.
    # This preserves any existing comments between [ and ] verbatim.
    line_start = last_nl + 1
    indent_end = line_start
    while indent_end < close_pos and raw[indent_end] in " \t":
        indent_end += 1
    bracket_indent = raw[line_start:indent_end]
    elem_indent = bracket_indent + "  "
    return raw[:line_start] + f"{elem_indent}{element}\n" + raw[line_start:]


def _append_into_nonempty_array(
    raw: str, array_start: int, close_pos: int, element: str
) -> str:
    """Append element after the last item in a non-empty array."""
    i = array_start + 1
    last_value_end = array_start + 1
    elem_indent = "  "
    while True:
        i = _skip_ws_comments(raw, i)
        if i >= close_pos or raw[i] == "]":
            break
        # Capture this element's line indent for the new element
        line_start = raw.rfind("\n", array_start, i)
        if line_start != -1:
            line_start += 1
            indent_end = line_start
            while indent_end < len(raw) and raw[indent_end] in " \t":
                indent_end += 1
            elem_indent = raw[line_start:indent_end]
        last_value_end = _skip_jsonc_value(raw, i)
        i = _skip_ws_comments(raw, last_value_end)
        if i < close_pos and raw[i] == ",":
            i += 1
        else:
            break
    trailing = raw[last_value_end:close_pos]
    insert_text = f",\n{elem_indent}{element}"
    return raw[:last_value_end] + insert_text + trailing + raw[close_pos:]


def _append_jsonc_instruction(
    raw: str, canonical_path: str
) -> str | None | _SurgicalFail:
    """Surgically insert *canonical_path* into the ``instructions`` array in JSONC text.

    Returns:
        ``None``:              Path already present — no write needed.
        ``str``:               New raw JSONC text with the path inserted.
        ``_SURGICAL_FAIL``:    Safe insertion is not possible; do not rewrite.
    """
    parsed = _parse_jsonc_root(raw)
    if isinstance(parsed, _SurgicalFail):
        return _SURGICAL_FAIL

    raw_instr = parsed.get("instructions")
    existing: list[str] = (
        [x for x in raw_instr if isinstance(x, str)]
        if isinstance(raw_instr, list)
        else []
    )
    if canonical_path in existing:
        return None

    brace_pos = _find_root_brace(raw)
    if isinstance(brace_pos, _SurgicalFail):
        return _SURGICAL_FAIL

    element = json.dumps(canonical_path)
    location = _locate_instructions_array(raw, brace_pos)
    if isinstance(location, _SurgicalFail):
        return _SURGICAL_FAIL
    if location is None:
        return _insert_new_instructions_member(raw, brace_pos, element)

    start, end = location
    close_pos = end - 1
    if _skip_ws_comments(raw, start + 1) >= close_pos:
        return _append_into_empty_array(raw, start, close_pos, element)
    return _append_into_nonempty_array(raw, start, close_pos, element)


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


def _as_object_dict(value: object) -> dict[str, object]:
    """Return *value* when it is a mapping, otherwise a fresh empty dict.

    Used to coerce untrusted JSON payloads: a malformed ``mcp.json`` may hold a
    list, string or ``null`` where an object is expected, and those must degrade
    to an empty dict rather than raising ``AttributeError`` downstream.
    """
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


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
        existing = _as_object_dict(json.loads(mcp_config.read_text()))
        servers = _as_object_dict(existing.get("servers"))
        if servers.get("cosalette") == cos_cfg:
            return None
        servers["cosalette"] = cos_cfg
        existing["servers"] = servers
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
    _atomic_write_text(mcp_config, content)
    typer.echo("✅ Configured .vscode/mcp.json for cosalette MCP server")


def _apply_surgical_jsonc_edit(
    config_path: Path, filename: str, canonical_path: str
) -> None:
    """Validate, surgically edit, and atomically write a JSONC config file."""
    raw = config_path.read_text()
    try:
        parsed = json.loads(_strip_jsonc_comments(raw))
    except json.JSONDecodeError:
        typer.echo(
            f"\u2757\ufe0f  Skipping {filename}: file contains malformed JSON; "
            "fix the file manually to preserve existing settings"
        )
        return
    if not isinstance(parsed, dict):
        typer.echo(
            f"\u2757\ufe0f  Skipping {filename}: top-level JSON value must be an "
            "object; fix the file manually to preserve existing settings"
        )
        return
    result = _append_jsonc_instruction(raw, canonical_path)
    if result is None:
        return
    if isinstance(result, _SurgicalFail):
        typer.echo(
            f"\u2757\ufe0f  Skipping {filename}: could not safely edit JSONC "
            f'without losing comments; add "{canonical_path}" to '
            '"instructions" manually'
        )
        return
    _atomic_write_text(config_path, result)
    typer.echo(f"\u2705 Configured {filename} for cosalette instructions")


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
        if strip_comments:
            _apply_surgical_jsonc_edit(config_path, filename, canonical_path)
            return
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
    _atomic_write_text(config_path, content)
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
