#!/usr/bin/env python3
"""Rasterize brand SVGs to PNG.

Companion to generate_brand_svgs.py — edit design tokens there,
then run this script to produce the PNGs.

Usage:
    uv run scripts/render_brand_assets.py          # render all
    uv run scripts/render_brand_assets.py hero      # hero banners only
    uv run scripts/render_brand_assets.py social    # social preview only
"""

from __future__ import annotations

import sys
from pathlib import Path

import cairosvg

BRAND_DIR = Path("docs/assets/images/brand")

ASSETS: dict[str, list[tuple[str, int, int]]] = {
    # key: [(svg_stem, width, height), ...]
    "hero": [
        ("hero-banner-dark", 1280, 320),
        ("hero-banner-light", 1280, 320),
    ],
    "social": [
        ("social-preview", 1280, 640),
    ],
    "docs": [
        ("docs-hero-dark", 800, 300),
        ("docs-hero-light", 800, 300),
    ],
}


def render(groups: list[str]) -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    for group in groups:
        for stem, w, h in ASSETS[group]:
            svg = BRAND_DIR / f"{stem}.svg"
            png = BRAND_DIR / f"{stem}.png"
            if not svg.exists():
                print(f"SKIP  {svg}  (not found)")
                continue
            cairosvg.svg2png(
                url=str(svg), write_to=str(png), output_width=w, output_height=h
            )
            print(f"OK    {png}  ({w}×{h})")


if __name__ == "__main__":
    requested = sys.argv[1:] or list(ASSETS)
    unknown = [g for g in requested if g not in ASSETS]
    if unknown:
        sys.exit(
            f"Unknown group(s): {', '.join(unknown)}. Choose from: {', '.join(ASSETS)}"
        )
    render(requested)
