#!/usr/bin/env python3
"""Nav-orphan checker: fail if any docs/**/*.md is neither in nav nor allowlisted."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

# ---------------------------------------------------------------------------
# Allowlist — deliberately-unlisted files that are still published.
# Each entry is a path relative to docs/ using forward slashes.
# ---------------------------------------------------------------------------

# Directory prefixes: any file whose relative path starts with one of these is
# allowed without appearing in nav.
ALLOWLISTED_PREFIXES: tuple[str, ...] = (
    "adr/",  # ADRs are linked from concept/guide prose, not nav-listed
    "maintenance/",  # Maintainer and contributor guides; not part of user nav
    "testing/",  # Test-file template; internal reference only
    "assets/",  # Asset partials (brand identity etc.); not standalone pages
    "security/",  # Audit charter/threat model/report; linked from SECURITY.md
)

# Exact files that are allowlisted for specific reasons.
ALLOWLISTED_EXACT: frozenset[str] = frozenset(
    {
        # Canonical AI-agent framework reference; kept as source-of-truth and
        # referenced by tooling, but intentionally excluded from the public nav.
        "reference/cosalette-framework-reference.instructions.md",
    }
)


def _collect_nav_paths(nav: object) -> list[str]:
    """Recursively collect every .md string value from the nav structure."""
    paths: list[str] = []
    if isinstance(nav, str):
        if nav.endswith(".md"):
            paths.append(nav)
    elif isinstance(nav, list):
        for item in nav:
            paths.extend(_collect_nav_paths(item))
    elif isinstance(nav, dict):
        for value in nav.values():
            paths.extend(_collect_nav_paths(value))
    return paths


def _is_allowlisted(rel: str) -> bool:
    if rel in ALLOWLISTED_EXACT:
        return True
    return any(rel.startswith(prefix) for prefix in ALLOWLISTED_PREFIXES)


def main() -> None:
    repo_root = Path(__file__).parent.parent
    docs_dir = repo_root / "docs"
    toml_path = repo_root / "zensical.toml"

    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    nav = config.get("project", {}).get("extra", {}).get("nav", [])
    nav_paths: set[str] = set(_collect_nav_paths(nav))

    all_md = sorted(docs_dir.rglob("*.md"))
    orphans: list[str] = []
    allowlisted_count = 0

    for md in all_md:
        rel = md.relative_to(docs_dir).as_posix()
        if rel in nav_paths:
            continue
        if _is_allowlisted(rel):
            allowlisted_count += 1
            continue
        orphans.append(rel)

    if orphans:
        print(
            "nav-orphan check: FAILED — the following files are not in nav "
            "(zensical.toml) and not on the allowlist.\n"
            "Add them to nav or to ALLOWLISTED_PREFIXES / ALLOWLISTED_EXACT "
            "in scripts/check_nav_orphans.py:",
            file=sys.stderr,
        )
        for path in orphans:
            print(f"  {path}", file=sys.stderr)
        sys.exit(1)

    print(
        f"nav-orphan check: OK ({len(nav_paths)} pages in nav, "
        f"{allowlisted_count} allowlisted)"
    )


if __name__ == "__main__":
    main()
