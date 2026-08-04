"""Render an ADR from a validated JSON input file.

Usage:
    uv run scripts/render_adr.py INPUT_JSON
    uv run scripts/render_adr.py INPUT_JSON --adr-dir docs/adr

The script reads a JSON file conforming to
.github/agents/schemas/adr-input.schema.json, validates it structurally,
and writes canonical ADR Markdown.

Operations:
  - type=new        → creates docs/adr/ADR-NNN-slug.md (auto-numbered)
  - type=supersede  → creates new ADR + updates superseded ADR status
  - type=amendment  → appends amendment section to existing ADR
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCALE_LEGEND = "_Scale: 1 (poor) to 5 (excellent)_"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require(data: dict[str, Any], key: str, label: str = "") -> Any:
    """Raise if *key* is missing from *data*."""
    if key not in data:
        ctx = f" (in {label})" if label else ""
        msg = f"Missing required field '{key}'{ctx}"
        raise ValueError(msg)
    return data[key]


def _validate_new_or_supersede(data: dict[str, Any]) -> None:
    """Structural validation for new/supersede ADR inputs."""
    for field in (
        "title",
        "date",
        "status",
        "impact",
        "context",
        "decision",
        "decision_drivers",
        "considered_options",
        "consequences_positive",
        "consequences_negative",
        "frontmatter",
    ):
        _require(data, field)

    impact = data["impact"]
    options = data["considered_options"]
    matrix = data.get("decision_matrix")

    # Impact-driven matrix enforcement.
    if impact in ("moderate", "high") and not matrix:
        msg = f"decision_matrix is required for impact={impact!r}"
        raise ValueError(msg)

    if impact == "high":
        if matrix and len(matrix) < 5:
            msg = "high-impact ADRs require ≥5 decision matrix criteria"
            raise ValueError(msg)
        if len(options) < 3:
            msg = "high-impact ADRs require ≥3 considered options"
            raise ValueError(msg)
    elif impact == "moderate" and matrix and len(matrix) < 3:
        msg = "moderate-impact ADRs require ≥3 decision matrix criteria"
        raise ValueError(msg)

    # Validate per-option required fields.
    for i, opt in enumerate(options):
        for opt_field in ("name", "description", "advantages", "disadvantages"):
            if opt_field not in opt:
                msg = f"Missing required field 'considered_options[{i}].{opt_field}'"
                raise ValueError(msg)

    # Exactly one chosen option.
    chosen = [o for o in options if o.get("chosen")]
    if len(chosen) != 1:
        msg = (
            f"Exactly one considered option must have chosen=true (found {len(chosen)})"
        )
        raise ValueError(msg)

    # Validate matrix score keys match option names and values are numeric.
    if matrix:
        option_names = {o["name"] for o in options}
        for i, row in enumerate(matrix):
            score_keys = set(row["scores"].keys())
            if score_keys != option_names:
                extra = score_keys - option_names
                missing = option_names - score_keys
                parts = []
                if extra:
                    parts.append(f"unknown: {sorted(extra)}")
                if missing:
                    parts.append(f"missing: {sorted(missing)}")
                msg = (
                    f"Matrix row '{row['criterion']}' score keys don't match "
                    f"option names ({', '.join(parts)})"
                )
                raise ValueError(msg)
            for opt_name, score in row["scores"].items():
                if not isinstance(score, (int, float)) or isinstance(score, bool):
                    got = type(score).__name__
                    msg = (
                        f"decision_matrix[{i}].scores.{opt_name}"
                        f" must be a number, got {got!r}"
                    )
                    raise ValueError(msg)

    if data["type"] == "supersede":
        _require(data, "supersedes_adr")


def _validate_amendment(data: dict[str, Any]) -> None:
    """Structural validation for amendment inputs."""
    for field in (
        "target_adr",
        "amendment_scope",
        "amendment_date",
        "amendment_content",
    ):
        _require(data, field)

    scope = data["amendment_scope"]
    content = data["amendment_content"]

    if scope in ("additive", "corrective"):
        _require(data, "amendment_rationale", "amendment")
    if scope == "corrective":
        _require(data, "amendment_justification", "corrective amendment")

    # Scope restrictions.
    if scope == "minor":
        for forbidden in (
            "additional_options",
            "additional_matrix_rows",
            "revised_decision",
            "revised_decision_code_example",
            "revised_decision_code_language",
            "sub_decisions",
        ):
            if forbidden in content:
                msg = f"'{forbidden}' is not allowed in a minor amendment"
                raise ValueError(msg)

    if scope == "additive":
        for forbidden in (
            "revised_decision",
            "revised_decision_code_example",
            "revised_decision_code_language",
        ):
            if forbidden in content:
                msg = (
                    f"'{forbidden}' is not allowed in an additive"
                    " amendment — use corrective or supersede"
                )
                raise ValueError(msg)


def validate(data: dict[str, Any]) -> None:
    """Run structural validation on the input JSON."""
    adr_type = _require(data, "type")
    if adr_type in ("new", "supersede"):
        _validate_new_or_supersede(data)
    elif adr_type == "amendment":
        _validate_amendment(data)
    else:
        msg = f"Unknown ADR type: {adr_type!r}"
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Auto-numbering
# ---------------------------------------------------------------------------


def next_adr_number(adr_dir: Path) -> int:
    """Scan *adr_dir* for the highest ADR number and return the next one."""
    pattern = re.compile(r"^ADR-(\d{3})")
    highest = 0
    for path in adr_dir.iterdir():
        if m := pattern.match(path.name):
            highest = max(highest, int(m.group(1)))
    return highest + 1


def slugify(title: str) -> str:
    """Convert a title to a kebab-case slug.

    Returns 'untitled' for empty or non-ASCII-only inputs.
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


# ---------------------------------------------------------------------------
# Markdown rendering — new / supersede
# ---------------------------------------------------------------------------


def _render_frontmatter(
    data: dict[str, Any], *, date: str, status: str, impact: str
) -> str:
    """Render YAML frontmatter block."""
    tags = data.get("frontmatter", {}).get("tags", [])
    lines = ["---"]
    lines.append(f"status: {status}")
    lines.append(f"date: {date}")
    lines.append(f"impact: {impact}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines.append("---")
    return "\n".join(lines)


def _render_single_option(
    opt: dict[str, Any], *, prefix: str = "", suffix: str = ""
) -> list[str]:
    """Render a single option block (shared by new ADR and amendment)."""
    lines: list[str] = []
    chosen = " (chosen)" if opt.get("chosen") else ""
    lines.append(f"{prefix}{opt['name']}{chosen}{suffix}")
    lines.append("")
    lines.append(opt["description"])
    lines.append("")
    adv = "; ".join(opt["advantages"])
    lines.append(f"- *Advantages:* {adv}")
    dis = "; ".join(opt["disadvantages"])
    lines.append(f"- *Disadvantages:* {dis}")
    lines.append("")
    return lines


def _render_options(options: list[dict[str, Any]]) -> str:
    """Render ## Considered Options section."""
    lines: list[str] = ["## Considered Options", ""]
    for i, opt in enumerate(options, 1):
        lines.extend(_render_single_option(opt, prefix=f"### Option {i}: "))
    return "\n".join(lines)


def _render_matrix(matrix: list[dict[str, Any]], options: list[dict[str, Any]]) -> str:
    """Render ## Decision Matrix section."""
    if not matrix:
        return ""

    option_names = [o["name"] for o in options]
    lines = ["## Decision Matrix", ""]

    # Header row.
    header = "| Criterion | " + " | ".join(option_names) + " |"
    sep = "| " + " | ".join(["---"] + ["---"] * len(option_names)) + " |"
    lines.extend([header, sep])

    # Data rows.
    for row in matrix:
        scores_str = " | ".join(
            str(row["scores"].get(name, "—")) for name in option_names
        )
        lines.append(f"| {row['criterion']} | {scores_str} |")

    lines.append("")
    lines.append(SCALE_LEGEND)
    lines.append("")
    return "\n".join(lines)


def _render_consequences(positive: list[str], negative: list[str]) -> str:
    """Render ## Consequences section."""
    lines = ["## Consequences", "", "### Positive", ""]
    for item in positive:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("### Negative")
    lines.append("")
    for item in negative:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_new_adr(data: dict[str, Any], number: int) -> str:
    """Render a complete new or superseding ADR as Markdown."""
    title = data["title"]
    date = data["date"]
    status = data["status"]
    impact = data["impact"]

    parts: list[str] = []

    # Frontmatter.
    parts.append(_render_frontmatter(data, date=date, status=status, impact=impact))
    parts.append("")

    # Title.
    parts.append(f"# ADR-{number:03d}: {title}")
    parts.append("")

    # Status.
    status_line = f"{status} **Date:** {date}"
    if data["type"] == "supersede":
        superseded = data["supersedes_adr"]
        status_line += f" | Supersedes {superseded}"
    parts.append("## Status")
    parts.append("")
    parts.append(status_line)
    parts.append("")

    # Context.
    parts.append("## Context")
    parts.append("")
    parts.append(data["context"])
    parts.append("")

    # Decision.
    parts.append("## Decision")
    parts.append("")
    parts.append(data["decision"])
    if code := data.get("decision_code_example"):
        lang = data.get("decision_code_language", "python")
        parts.append("")
        parts.append(f"```{lang}")
        parts.append(code)
        parts.append("```")
    parts.append("")

    # Decision Drivers.
    parts.append("## Decision Drivers")
    parts.append("")
    for driver in data["decision_drivers"]:
        parts.append(f"- {driver}")
    parts.append("")

    # Considered Options.
    parts.append(_render_options(data["considered_options"]))

    # Decision Matrix.
    matrix_md = _render_matrix(
        data.get("decision_matrix", []), data["considered_options"]
    )
    if matrix_md:
        parts.append(matrix_md)

    # Consequences.
    parts.append(
        _render_consequences(
            data["consequences_positive"], data["consequences_negative"]
        )
    )

    # Date stamp.
    parts.append(f"_{date}_")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Markdown rendering — amendment
# ---------------------------------------------------------------------------


def render_amendment(data: dict[str, Any]) -> str:
    """Render the amendment block to append to an existing ADR."""
    scope = data["amendment_scope"]
    date = data["amendment_date"]
    content = data["amendment_content"]

    scope_label = scope.capitalize()
    lines = [f"## Amendment ({date}) — {scope_label}", ""]

    # Rationale (additive/corrective).
    if rationale := data.get("amendment_rationale"):
        lines.append(f"**Rationale:** {rationale}")
        lines.append("")

    # Justification (corrective).
    if justification := data.get("amendment_justification"):
        lines.append(
            f"> **Justification for amendment (not supersession):** {justification}"
        )
        lines.append("")

    # Revised decision.
    if revised := content.get("revised_decision"):
        lines.append("### Revised Decision")
        lines.append("")
        lines.append(revised)
        if code := content.get("revised_decision_code_example"):
            lang = content.get("revised_decision_code_language", "python")
            lines.append("")
            lines.append(f"```{lang}")
            lines.append(code)
            lines.append("```")
        lines.append("")

    # Sub-decisions.
    for sub in content.get("sub_decisions", []):
        lines.append(f"### Additional Sub-Decision: {sub['title']}")
        lines.append("")
        lines.append(sub["description"])
        lines.append("")

    # Additional options.
    if add_opts := content.get("additional_options"):
        lines.append("### Additional Considered Options")
        lines.append("")
        for opt in add_opts:
            lines.extend(_render_single_option(opt, prefix="**", suffix="**"))

    # Additional matrix rows.
    if add_rows := content.get("additional_matrix_rows"):
        lines.append("### Additional Decision Matrix Rows")
        lines.append("")
        for row in add_rows:
            scores_parts = [f"{k}: {v}" for k, v in row["scores"].items()]
            lines.append(f"- **{row['criterion']}** — {', '.join(scores_parts)}")
        lines.append("")

    # Notes.
    for note in content.get("notes", []):
        lines.append(f'!!! note "Editorial note ({date})"')
        # Indent the note body for the admonition.
        for line in note.splitlines():
            lines.append(f"    {line}" if line else "")
        lines.append("")

    # Additional consequences.
    if pos := content.get("additional_consequences_positive"):
        lines.append("### Additional Positive Consequences")
        lines.append("")
        for item in pos:
            lines.append(f"- {item}")
        lines.append("")

    if neg := content.get("additional_consequences_negative"):
        lines.append("### Additional Negative Consequences")
        lines.append("")
        for item in neg:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def find_adr_file(adr_dir: Path, adr_ref: str) -> Path:
    """Find the file matching an ADR reference like 'ADR-006'."""
    matches = [p for p in sorted(adr_dir.iterdir()) if p.name.startswith(adr_ref)]
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        msg = f"Multiple files found for {adr_ref} in {adr_dir}: {names}"
        raise ValueError(msg)
    if not matches:
        msg = f"Cannot find file for {adr_ref} in {adr_dir}"
        raise FileNotFoundError(msg)
    return matches[0]


def update_superseded_status(adr_path: Path, new_adr_ref: str) -> None:
    """Update the Status section of a superseded ADR."""
    text = adr_path.read_text(encoding="utf-8")

    # Match the Status section and replace the status line.
    # Pattern: line starting with "Accepted" or "Proposed" after "## Status"
    pattern = re.compile(
        r"(## Status\s*\n\s*\n)"
        r"(Accepted|Proposed)(.*)",
        re.MULTILINE,
    )
    replacement = rf"\1Superseded by {new_adr_ref}\3"
    new_text, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        msg = f"Could not find Status section in {adr_path}"
        raise ValueError(msg)

    # Also update frontmatter status if present.
    new_text = re.sub(
        r"^status:\s*(Accepted|Proposed)",
        f"status: Superseded by {new_adr_ref}",
        new_text,
        count=1,
        flags=re.MULTILINE,
    )

    adr_path.write_text(new_text, encoding="utf-8")


def update_amendment_status_line(adr_path: Path, amendment_date: str) -> None:
    """Append amendment date to the Status line of an existing ADR."""
    text = adr_path.read_text(encoding="utf-8")

    # Find the status line (first non-empty line after ## Status).
    # Also match Superseded status lines.
    pattern = re.compile(
        r"(## Status\s*\n\s*\n)"
        r"((?:Accepted|Proposed|Superseded\b).*?)(\s*\n)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        msg = f"Could not find Status section in {adr_path}"
        raise ValueError(msg)

    existing_status = match.group(2)
    # Only add amended date if not already present for this date.
    if f"Amended **Date:** {amendment_date}" not in existing_status:
        new_status = f"{existing_status} | Amended **Date:** {amendment_date}"
        new_text = text[: match.start(2)] + new_status + text[match.end(2) :]
        adr_path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


_TRAILING_DATE_STAMP = re.compile(r"\n_\d{4}-\d{2}-\d{2}_\s*$")


def _handle_new_or_supersede(data: dict[str, Any], adr_dir: Path) -> int:
    """Handle new or supersede ADR creation. Returns exit code."""
    number = next_adr_number(adr_dir)
    slug = slugify(data["title"])
    filename = f"ADR-{number:03d}-{slug}.md"
    output_path = adr_dir / filename

    md = render_new_adr(data, number)
    output_path.write_text(md, encoding="utf-8")
    print(f"Created {output_path}")

    # Handle supersession: update the old ADR.
    if data["type"] == "supersede":
        old_ref = data["supersedes_adr"]
        old_path = find_adr_file(adr_dir, old_ref)
        new_ref = f"ADR-{number:03d}"
        update_superseded_status(old_path, new_ref)
        print(f"Updated {old_path} → Superseded by {new_ref}")

    return 0


def _handle_amendment(data: dict[str, Any], adr_dir: Path) -> int:
    """Handle amendment to an existing ADR. Returns exit code."""
    target_ref = data["target_adr"]
    target_path = find_adr_file(adr_dir, target_ref)

    amendment_md = render_amendment(data)

    # Read existing content once.
    existing = target_path.read_text(encoding="utf-8")

    # Strip trailing date stamp before appending amendment so it
    # doesn't end up buried in the middle of the document.
    existing_stripped = _TRAILING_DATE_STAMP.sub("", existing).rstrip()
    existing_stripped += "\n\n"
    existing_stripped += amendment_md

    target_path.write_text(existing_stripped + "\n", encoding="utf-8")
    update_amendment_status_line(target_path, data["amendment_date"])
    print(f"Amended {target_path}")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Render an ADR from a validated JSON input file.",
    )
    parser.add_argument(
        "input_json",
        type=Path,
        help="Path to the JSON input file.",
    )
    parser.add_argument(
        "--adr-dir",
        type=Path,
        default=Path("docs/adr"),
        help="Directory containing ADR files (default: docs/adr).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    input_path: Path = args.input_json
    adr_dir: Path = args.adr_dir

    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        return 1

    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Error: failed to parse {input_path}: {exc}", file=sys.stderr)
        return 1

    try:
        validate(data)
    except ValueError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1

    adr_type = data["type"]

    if adr_type in ("new", "supersede"):
        return _handle_new_or_supersede(data, adr_dir)
    elif adr_type == "amendment":
        return _handle_amendment(data, adr_dir)

    return 0  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
