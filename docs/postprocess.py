"""Post-processing step: auto-link bare ADR-NNN references in built HTML.

Run after `zensical build` to rewrite plain ``ADR-NNN`` text in the
``site/`` directory to hyperlinks pointing at the corresponding ADR page.
Text already inside ``<a>``, ``<code>``, ``<pre>``, ``<title>``, or
``<head>`` tags is left untouched.

Usage:
    uv run docs/postprocess.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SITE_DIR = Path(__file__).parent.parent / "site"
ADR_DIR = Path(__file__).parent / "adr"

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_ADR_RE = re.compile(r"\bADR-(\d+)\b")

# ADR filenames must follow the canonical pattern to be safe for href interpolation.
_VALID_SLUG_RE = re.compile(r"^ADR-\d{3}-[a-z0-9-]+$")

# Regions to skip: existing links, code spans, pre blocks, title, and the full
# head section (protects metadata, canonical URLs, and OG tags from rewriting).
_SKIP_RE = re.compile(
    r"<a\b[^>]*>.*?</a>"
    r"|<pre\b[^>]*>.*?</pre>"
    r"|<code\b[^>]*>.*?</code>"
    r"|<title\b[^>]*>.*?</title>"
    r"|<head\b[^>]*>.*?</head>",
    re.DOTALL,
)


def _build_slug_map() -> dict[str, str]:
    """Map zero-padded ADR number → filename stem (e.g. "038" → "ADR-038-...")."""
    slugs: dict[str, str] = {}
    for md_file in sorted(ADR_DIR.glob("ADR-*.md")):
        if not _VALID_SLUG_RE.match(md_file.stem):
            continue
        m = re.match(r"ADR-(\d+)", md_file.stem)
        if m:
            slugs[m.group(1).zfill(3)] = md_file.stem
    return slugs


def _link_adrs(html: str, prefix: str, slugs: dict[str, str]) -> str:
    """Replace bare ADR-NNN text with hyperlinks, skipping skip-zones."""

    def _link(m: re.Match[str]) -> str:  # type: ignore[type-arg]
        num = m.group(1).zfill(3)
        slug = slugs.get(num)
        if slug is None:
            return m.group(0)
        return f'<a href="{prefix}{slug}/">{m.group(0)}</a>'

    # Single pass: walk skip-zone boundaries, process plain-text gaps only.
    parts: list[str] = []
    last = 0
    for skip_match in _SKIP_RE.finditer(html):
        parts.append(_ADR_RE.sub(_link, html[last : skip_match.start()]))
        parts.append(skip_match.group(0))
        last = skip_match.end()
    parts.append(_ADR_RE.sub(_link, html[last:]))
    return "".join(parts)


def _adr_prefix(html_path: Path, site_dir: Path) -> str:
    """Relative path prefix from an HTML file to the site/adr/ directory.

    Uses the number of path components between *site_dir* and *html_path*
    (excluding the filename itself) to build the correct ``../`` prefix.

    Examples (with use_directory_urls):
        site/index.html                  → depth 0 → "adr/"
        site/reference/api/index.html    → depth 2 → "../../adr/"
    """
    depth = len(html_path.relative_to(site_dir).parts) - 1
    return "../" * depth + "adr/"


def process(site_dir: Path = SITE_DIR) -> int:
    """Walk *site_dir*, rewrite ADR references, return count of files changed."""
    slugs = _build_slug_map()
    if not slugs:
        print("WARNING: no ADR files found — nothing to link", file=sys.stderr)
        return 0

    changed = 0
    for html_path in site_dir.rglob("*.html"):
        original = html_path.read_text(encoding="utf-8")
        prefix = _adr_prefix(html_path, site_dir)
        updated = _link_adrs(original, prefix, slugs)
        if updated != original:
            html_path.write_text(updated, encoding="utf-8")
            changed += 1

    return changed


if __name__ == "__main__":
    n = process()
    print(f"ADR links: {n} file(s) updated in {SITE_DIR}")
