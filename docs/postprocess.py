"""Post-processing step: auto-link bare ADR-NNN references in built HTML.

Run after `zensical build` to rewrite plain ``ADR-NNN`` text in the
``site/`` directory to hyperlinks pointing at the corresponding ADR page.
Text already inside ``<a>``, ``<code>``, or ``<pre>`` tags is left
untouched.

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

# Regions to preserve: existing links, code spans, pre blocks.
_SKIP_RE = re.compile(
    r"<a\b[^>]*>.*?</a>|<pre\b[^>]*>.*?</pre>|<code\b[^>]*>.*?</code>",
    re.DOTALL,
)


def _build_slug_map() -> dict[str, str]:
    """Map zero-padded ADR number → filename stem (e.g. "038" → "ADR-038-...")."""
    slugs: dict[str, str] = {}
    for md_file in sorted(ADR_DIR.glob("ADR-*.md")):
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

    # Split on skip-zones; even-indexed tokens are plain text to process.
    tokens = _SKIP_RE.split(html)
    skips = _SKIP_RE.findall(html)
    parts: list[str] = []
    for i, token in enumerate(tokens):
        parts.append(_ADR_RE.sub(_link, token))
        if i < len(skips):
            parts.append(skips[i])
    return "".join(parts)


def _adr_prefix(html_path: Path) -> str:
    """Relative path prefix from an HTML file to the site/adr/ directory."""
    # Under use_directory_urls, each page lives in its own subdirectory:
    # e.g. site/reference/api/index.html → depth from site/ = 2 → "../../adr/"
    depth = len(html_path.relative_to(SITE_DIR).parent.parts)
    return "../" * depth + "adr/"


def process(site_dir: Path = SITE_DIR) -> int:
    """Walk *site_dir*, rewrite ADR references, return count of files changed."""
    slugs = _build_slug_map()
    if not slugs:
        print("WARNING: no ADR files found — nothing to link", file=sys.stderr)
        return 0

    changed = 0
    for html_path in sorted(site_dir.rglob("*.html")):
        original = html_path.read_text(encoding="utf-8")
        prefix = _adr_prefix(html_path)
        updated = _link_adrs(original, prefix, slugs)
        if updated != original:
            html_path.write_text(updated, encoding="utf-8")
            changed += 1

    return changed


if __name__ == "__main__":
    n = process()
    print(f"ADR links: {n} file(s) updated in {SITE_DIR}")
