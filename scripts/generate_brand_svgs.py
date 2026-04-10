#!/usr/bin/env python3
"""Generate brand banner SVGs from parameterized design tokens.

Encodes all design decisions (colors, sizes, positions, honeycomb geometry)
so future tweaks are parameter changes, not SVG surgery.

Usage:
    uv run scripts/generate_brand_svgs.py            # generate all SVGs
    uv run scripts/generate_brand_svgs.py hero        # hero banners only
    uv run scripts/generate_brand_svgs.py social      # social preview only
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

BRAND_DIR = Path("docs/assets/images/brand")

# ---------------------------------------------------------------------------
# Brand colors
# ---------------------------------------------------------------------------
AMBER = "#FFC105"
DARK_BG = "#0D0D0F"
DARK_FG = "#1A1A1F"
LIGHT_BG = "#FFFFFF"
TEXT_LIGHT = "#E6E6E6"
TEXT_DARK = "#1A1A1F"

# ---------------------------------------------------------------------------
# Honeycomb geometry (flat-top, side s=40)
# ---------------------------------------------------------------------------
HEX_SIDE = 40
TILE_W = 3 * HEX_SIDE  # 120
TILE_H = HEX_SIDE * math.sqrt(3)  # ~69.282
HALF_H = TILE_H / 2  # ~34.641

# ---------------------------------------------------------------------------
# Logomark paths (from logo-mark-dark.svg, 512×512 viewBox)
# ---------------------------------------------------------------------------
LOGOMARK_PATHS = dedent("""\
    <polyline points="396,236 332,124 180,124 116,236"/>
    <polyline points="116,276 180,388 332,388 396,276"/>
    <circle cx="52" cy="256" r="20"/>
    <circle cx="460" cy="256" r="20"/>
    <polyline points="72,256 190,256 208,278 224,256 248,162 272,350 292,215 306,248 320,256 440,256"/>""")

# ---------------------------------------------------------------------------
# Wordmark glyph paths (from logotype-dark.svg, viewBox 0 0 338 100)
# These are the exact Inter Medium outlines converted to glyph paths.
# ---------------------------------------------------------------------------
GLYPH_PATHS = dedent("""\
    <path transform="translate(0,0)" d="M618 -23Q460 -23 343.0 49.0Q226 121 161.0 250.5Q96 380 96 553Q96 727 161.0 857.5Q226 988 343.0 1060.0Q460 1132 618 1132Q792 1132 918.5 1044.5Q1045 957 1086 802L877 752Q854 839 787.5 892.5Q721 946 619 946Q518 946 451.5 893.0Q385 840 352.5 750.5Q320 661 320 553Q320 446 352.5 357.5Q385 269 451.5 216.0Q518 163 619 163Q723 163 791.0 218.5Q859 274 881 365L1089 315Q1048 157 921.0 67.0Q794 -23 618 -23Z"/>
    <path transform="translate(1182,0)" d="M618 -23Q461 -23 343.5 49.0Q226 121 161.0 250.5Q96 380 96 553Q96 727 161.0 857.5Q226 988 343.5 1060.0Q461 1132 618 1132Q776 1132 893.5 1060.0Q1011 988 1076.0 857.5Q1141 727 1141 553Q1141 380 1076.0 250.5Q1011 121 893.5 49.0Q776 -23 618 -23ZM618 163Q720 163 786.5 216.5Q853 270 885.5 358.5Q918 447 918 553Q918 660 885.5 749.5Q853 839 786.5 892.5Q720 946 618 946Q517 946 451.0 892.5Q385 839 352.5 749.5Q320 660 320 553Q320 447 352.5 358.5Q385 270 451.0 216.5Q517 163 618 163Z"/>
    <path transform="translate(2419,0)" d="M549 -23Q367 -23 247.5 55.5Q128 134 98 283L305 326Q350 155 551 155Q658 155 720.5 199.5Q783 244 783 306Q783 412 626 447L438 490Q130 559 130 799Q130 899 185.5 974.0Q241 1049 338.5 1090.5Q436 1132 563 1132Q744 1132 847.0 1054.5Q950 977 986 853L789 810Q768 869 716.0 913.5Q664 958 565 958Q474 958 413.0 917.0Q352 876 352 813Q352 757 392.5 722.5Q433 688 525 667L705 627Q1010 558 1010 325Q1010 223 951.5 144.5Q893 66 789.0 21.5Q685 -23 549 -23Z"/>
    <path transform="translate(3522,0)" d="M461 -25Q354 -25 268.5 14.5Q183 54 133.0 130.0Q83 206 83 316Q83 411 120.0 472.5Q157 534 219.0 569.5Q281 605 357.5 623.0Q434 641 513 650Q661 667 728.0 679.5Q795 692 795 751V757Q795 850 740.5 901.5Q686 953 581 953Q471 953 408.0 905.0Q345 857 322 800L115 854Q153 954 224.0 1015.0Q295 1076 386.5 1104.0Q478 1132 578 1132Q645 1132 721.0 1116.0Q797 1100 864.0 1058.5Q931 1017 973.0 940.0Q1015 863 1015 742V0H802V153H791Q758 87 677.5 31.0Q597 -25 461 -25ZM510 152Q599 152 663.0 187.5Q727 223 761.5 280.0Q796 337 796 403V546Q780 530 727.5 518.5Q675 507 617.0 499.0Q559 491 525 487Q432 474 365.5 436.5Q299 399 299 313Q299 233 358.5 192.5Q418 152 510 152Z"/>
    <path transform="translate(4685,0)" d="M368 1490V0H148V1490Z"/>
    <path transform="translate(5201,0)" d="M630 -23Q465 -23 345.0 48.0Q225 119 160.5 248.0Q96 377 96 551Q96 723 159.5 854.0Q223 985 339.0 1058.5Q455 1132 611 1132Q738 1132 852.5 1075.5Q967 1019 1038.5 895.5Q1110 772 1110 570V492H317Q323 330 409.0 244.5Q495 159 632 159Q724 159 790.5 199.0Q857 239 886 317L1093 268Q1053 137 931.0 57.0Q809 -23 630 -23ZM318 656H892Q880 788 809.5 869.0Q739 950 612 950Q480 950 403.5 863.5Q327 777 318 656Z"/>
    <path transform="translate(6404,0)" d="M403 1384V302Q403 235 431.5 203.5Q460 172 525 172Q543 172 570.5 176.0Q598 180 618 184L657 8Q622 -4 581.5 -9.5Q541 -15 503 -15Q351 -15 267.0 63.5Q183 142 183 284V1384Z"/>
    <path transform="translate(7101,0)" d="M403 1384V302Q403 235 431.5 203.5Q460 172 525 172Q543 172 570.5 176.0Q598 180 618 184L657 8Q622 -4 581.5 -9.5Q541 -15 503 -15Q351 -15 267.0 63.5Q183 142 183 284V1384Z"/>
    <path transform="translate(7798,0)" d="M630 -23Q465 -23 345.0 48.0Q225 119 160.5 248.0Q96 377 96 551Q96 723 159.5 854.0Q223 985 339.0 1058.5Q455 1132 611 1132Q738 1132 852.5 1075.5Q967 1019 1038.5 895.5Q1110 772 1110 570V492H317Q323 330 409.0 244.5Q495 159 632 159Q724 159 790.5 199.0Q857 239 886 317L1093 268Q1053 137 931.0 57.0Q809 -23 630 -23ZM318 656H892Q880 788 809.5 869.0Q739 950 612 950Q480 950 403.5 863.5Q327 777 318 656Z"/>""")

# ECG wave ligature polyline (overlays the "tt" in the wordmark)
ECG_WAVE = (
    "203.5,57.8 231.9,57.8 243.6,59.8 250.4,57.8 "
    "260.6,43.8 270.8,66.8 279.4,52.8 285.3,57.0 295.3,57.8 317.0,57.8"
)


# ---------------------------------------------------------------------------
# Waveform accent — ECG pulse inlined at a hex-center position
# ---------------------------------------------------------------------------
def _ecg_accent(cx: float, cy: float) -> str:
    """Return a polyline for a small ECG waveform accent centered at (cx, cy).

    The waveform spans ~60px wide × ~30px tall, centered on the hex cell.
    """
    # Relative offsets from center for a 60px-wide ECG blip
    offsets = [
        (-29.4, 0),
        (-10.6, 0),
        (-7.7, 3.5),
        (-5.1, 0),
        (-1.3, -15),
        (2.6, 15),
        (5.8, -6.6),
        (8.0, -1.3),
        (10.2, 0),
        (29.4, 0),
    ]
    pts = " ".join(f"{cx + dx:.1f},{cy + dy:.3f}" for dx, dy in offsets)
    return f'    <polyline points="{pts}"/>'


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class WaveformAccent:
    """A waveform accent at a hex-center grid position."""

    cx: float
    cy: float


@dataclass
class BannerSpec:
    """All parameters needed to generate one banner SVG."""

    width: int
    height: int
    title: str
    bg_color: str
    stroke_color: str
    fill_color: str
    text_color: str
    tagline: str

    # Honeycomb
    honeycomb_stroke_width: float = 1.6
    honeycomb_opacity: float = 0.055

    # Lockup position
    lockup_x: float = 296
    lockup_y: float = 76

    # Logomark
    logomark_scale: float = 0.25

    # Wordmark nested SVG (viewBox is always 0 0 338 100)
    wordmark_x: float = 140
    wordmark_y: float = -30
    wordmark_w: float = 540
    wordmark_h: float = 160

    # Tagline
    tagline_y: float = 230
    tagline_font_size: int = 20
    tagline_opacity: float = 0.82

    # Waveform accents
    waveform_accents: list[WaveformAccent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SVG generation
# ---------------------------------------------------------------------------
def generate_svg(spec: BannerSpec) -> str:
    accents = "\n".join(_ecg_accent(a.cx, a.cy) for a in spec.waveform_accents)

    return f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {spec.width} {spec.height}" width="{spec.width}" height="{spec.height}">
  <title>{spec.title}</title>
  <rect width="{spec.width}" height="{spec.height}" fill="{spec.bg_color}"/>

  <defs>
    <!-- Honeycomb tile: flat-top hexagons, side s={HEX_SIDE}, tile {TILE_W}\u00d7{TILE_H:.3f} -->
    <pattern id="honeycomb" width="{TILE_W}" height="{TILE_H:.3f}" patternUnits="userSpaceOnUse">
      <polygon points="{TILE_W - 20},{HALF_H:.3f} {TILE_W - 40},{TILE_H:.3f} {HEX_SIDE},{TILE_H:.3f} 20,{HALF_H:.3f} {HEX_SIDE},0 {TILE_W - 40},0"
               fill="none" stroke="{spec.stroke_color}" stroke-width="{spec.honeycomb_stroke_width}"/>
      <line x1="20" y1="{HALF_H:.3f}" x2="0" y2="{HALF_H:.3f}"
            stroke="{spec.stroke_color}" stroke-width="{spec.honeycomb_stroke_width}"/>
      <line x1="{TILE_W - 20}" y1="{HALF_H:.3f}" x2="{TILE_W}" y2="{HALF_H:.3f}"
            stroke="{spec.stroke_color}" stroke-width="{spec.honeycomb_stroke_width}"/>
    </pattern>
  </defs>

  <!-- Honeycomb background -->
  <rect width="{spec.width}" height="{spec.height}" fill="url(#honeycomb)" opacity="{spec.honeycomb_opacity}"/>

  <!-- Waveform accents at hex-center positions (inlined for cairosvg compat) -->
  <g fill="none" stroke="{spec.stroke_color}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" opacity="{spec.honeycomb_opacity}">
{accents}
  </g>

  <!-- Lockup: logomark + wordmark -->
  <g transform="translate({spec.lockup_x}, {spec.lockup_y})">
    <g transform="scale({spec.logomark_scale})" fill="none" stroke="{spec.stroke_color}" stroke-width="14"
       stroke-linecap="round" stroke-linejoin="round">
{_indent(LOGOMARK_PATHS, 6)}
    </g>

    <svg x="{spec.wordmark_x}" y="{spec.wordmark_y}" width="{spec.wordmark_w}" height="{spec.wordmark_h}" viewBox="0 0 338 100" overflow="visible">
      <g transform="translate(10,78) scale(0.03515625,-0.03515625)" fill="{spec.fill_color}">
{_indent(GLYPH_PATHS, 8)}
      </g>
      <polyline points="{ECG_WAVE}"
                fill="none" stroke="{spec.fill_color}" stroke-width="5.8"
                stroke-linecap="butt" stroke-linejoin="round"/>
    </svg>
  </g>

  <!-- Tagline -->
  <text x="{spec.width // 2}" y="{spec.tagline_y}" text-anchor="middle"
        font-family="Inter, system-ui, -apple-system, sans-serif"
        font-size="{spec.tagline_font_size}" font-weight="400" fill="{spec.text_color}" letter-spacing="0.5"
        opacity="{spec.tagline_opacity}">{spec.tagline}</text>
</svg>
"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


# ---------------------------------------------------------------------------
# Banner specifications
# ---------------------------------------------------------------------------

# Hero banner waveform accents (hex-center lattice positions)
HERO_ACCENTS = [
    WaveformAccent(180.0, 34.641),
    WaveformAccent(1080.0, 69.282),
    WaveformAccent(60.0, 242.487),
    WaveformAccent(1140.0, 242.487),
    WaveformAccent(720.0, 277.128),
]

# Social preview waveform accents (more scattered for taller canvas)
SOCIAL_ACCENTS = [
    WaveformAccent(120.0, 69.282),
    WaveformAccent(1080.0, 69.282),
    WaveformAccent(60.0, 173.205),
    WaveformAccent(1200.0, 277.128),
    WaveformAccent(60.0, 519.615),
    WaveformAccent(1080.0, 554.256),
    WaveformAccent(660.0, 588.897),
]

HERO_TAGLINE = "An opinionated Python framework for IoT-to-MQTT bridges"
SOCIAL_TAGLINE = "A Python framework for IoT-to-MQTT bridges"


def hero_dark() -> BannerSpec:
    return BannerSpec(
        width=1280,
        height=320,
        title="cosalette \u2014 README hero banner (dark background)",
        bg_color=DARK_BG,
        stroke_color=AMBER,
        fill_color=AMBER,
        text_color=TEXT_LIGHT,
        tagline=HERO_TAGLINE,
        lockup_x=296,
        lockup_y=76,
        logomark_scale=0.25,
        wordmark_x=140,
        wordmark_y=-30,
        wordmark_w=540,
        wordmark_h=160,
        tagline_y=230,
        tagline_font_size=20,
        waveform_accents=HERO_ACCENTS,
    )


def hero_light() -> BannerSpec:
    return BannerSpec(
        width=1280,
        height=320,
        title="cosalette \u2014 README hero banner (light background)",
        bg_color=LIGHT_BG,
        stroke_color=DARK_FG,
        fill_color=DARK_FG,
        text_color=TEXT_DARK,
        tagline=HERO_TAGLINE,
        lockup_x=296,
        lockup_y=76,
        logomark_scale=0.25,
        wordmark_x=140,
        wordmark_y=-30,
        wordmark_w=540,
        wordmark_h=160,
        tagline_y=230,
        tagline_font_size=20,
        waveform_accents=HERO_ACCENTS,
    )


def social_preview() -> BannerSpec:
    return BannerSpec(
        width=1280,
        height=640,
        title="cosalette \u2014 GitHub social preview / OG image",
        bg_color=DARK_BG,
        stroke_color=AMBER,
        fill_color=AMBER,
        text_color=TEXT_LIGHT,
        tagline=SOCIAL_TAGLINE,
        lockup_x=115,
        lockup_y=175,
        logomark_scale=0.386,
        wordmark_x=216,
        wordmark_y=-46,
        wordmark_w=834,
        wordmark_h=247,
        tagline_y=435,
        tagline_font_size=36,
        waveform_accents=SOCIAL_ACCENTS,
    )


GROUPS: dict[str, list[tuple[str, BannerSpec]]] = {
    "hero": [
        ("hero-banner-dark", hero_dark()),
        ("hero-banner-light", hero_light()),
    ],
    "social": [
        ("social-preview", social_preview()),
    ],
}


# ---------------------------------------------------------------------------
# Docs hero system diagram (parameterized for dark/light variants)
# ---------------------------------------------------------------------------


@dataclass
class DiagramColors:
    """Color scheme for the docs hero system diagram."""

    bg: str
    stroke: str
    node_fill: str
    text: str
    brand_text: str
    command: str
    honeycomb_opacity: float = 0.04


# Waveform accents at hex-center positions (even row m=5: x = 120k)
DOCS_HERO_ACCENTS = [
    WaveformAccent(60.0, 34.641),
    WaveformAccent(720.0, 69.282),
    WaveformAccent(120.0, 346.410),
    WaveformAccent(600.0, 346.410),  # fixed: 660 was on hex edge, not center
]

DOCS_HERO_DARK = DiagramColors(
    bg=DARK_BG,
    stroke=AMBER,
    node_fill="#1A1A1F",
    text=TEXT_LIGHT,
    brand_text=AMBER,
    command="#FF9100",
)

DOCS_HERO_LIGHT = DiagramColors(
    bg=LIGHT_BG,
    stroke=DARK_FG,
    node_fill="#F5F5F5",
    text=TEXT_DARK,
    brand_text=DARK_FG,
    command="#C46200",
    honeycomb_opacity=0.06,
)


def generate_docs_hero(colors: DiagramColors) -> str:
    """Generate the docs hero system diagram SVG."""
    variant = "dark" if colors.bg == DARK_BG else "light"
    accents = "\n".join(_ecg_accent(a.cx, a.cy) for a in DOCS_HERO_ACCENTS)

    return f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 50 800 300" width="800" height="300">
  <title>cosalette \u2014 system diagram ({variant})</title>
  <rect width="800" height="400" fill="{colors.bg}"/>

  <defs>
    <pattern id="honeycomb" width="{TILE_W}" height="{TILE_H:.3f}" patternUnits="userSpaceOnUse">
      <polygon points="{TILE_W - 20},{HALF_H:.3f} {TILE_W - 40},{TILE_H:.3f} {HEX_SIDE},{TILE_H:.3f} 20,{HALF_H:.3f} {HEX_SIDE},0 {TILE_W - 40},0"
               fill="none" stroke="{colors.stroke}" stroke-width="1.6"/>
      <line x1="20" y1="{HALF_H:.3f}" x2="0" y2="{HALF_H:.3f}"
            stroke="{colors.stroke}" stroke-width="1.6"/>
      <line x1="{TILE_W - 20}" y1="{HALF_H:.3f}" x2="{TILE_W}" y2="{HALF_H:.3f}"
            stroke="{colors.stroke}" stroke-width="1.6"/>
    </pattern>
    <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5"
            markerWidth="8" markerHeight="8" orient="auto-start-reverse">
      <path d="M 0 1 L 10 5 L 0 9" fill="none" stroke="{colors.stroke}" stroke-width="1.5"/>
    </marker>
    <marker id="arrow-cmd" viewBox="0 0 10 10" refX="10" refY="5"
            markerWidth="8" markerHeight="8" orient="auto-start-reverse">
      <path d="M 0 1 L 10 5 L 0 9" fill="none" stroke="{colors.command}" stroke-width="1.5"/>
    </marker>
  </defs>

  <rect width="800" height="400" fill="url(#honeycomb)" opacity="{colors.honeycomb_opacity}"/>

  <!-- Devices node -->
  <g transform="translate(105, 200)">
    <polygon points="0,-65 56.3,-32.5 56.3,32.5 0,65 -56.3,32.5 -56.3,-32.5"
             fill="none" stroke="{colors.stroke}" stroke-width="2" opacity="0.6"/>
    <polygon points="0,-50 43.3,-25 43.3,25 0,50 -43.3,25 -43.3,-25"
             fill="{colors.node_fill}" stroke="{colors.stroke}" stroke-width="1.5" opacity="0.9"/>
    <circle cx="0" cy="-8" r="8" fill="none" stroke="{colors.stroke}" stroke-width="2"/>
    <line x1="0" y1="0" x2="0" y2="16" stroke="{colors.stroke}" stroke-width="2"/>
    <line x1="-10" y1="16" x2="10" y2="16" stroke="{colors.stroke}" stroke-width="2"/>
    <text y="90" text-anchor="middle" font-family="Inter, system-ui, sans-serif"
          font-size="14" font-weight="500" fill="{colors.text}" opacity="0.82">Devices</text>
  </g>

  <!-- cosalette node -->
  <g transform="translate(400, 200)">
    <polygon points="0,-90 77.9,-45 77.9,45 0,90 -77.9,45 -77.9,-45"
             fill="none" stroke="{colors.stroke}" stroke-width="2.5" opacity="0.7"/>
    <polygon points="0,-72 62.4,-36 62.4,36 0,72 -62.4,36 -62.4,-36"
             fill="{colors.node_fill}" stroke="{colors.stroke}" stroke-width="1.5" opacity="0.95"/>
    <polygon points="0,-32 27.7,-16 27.7,16 0,32 -27.7,16 -27.7,-16"
             fill="none" stroke="{colors.stroke}" stroke-width="2"/>
    <polyline points="-25,0 -12,0 -7,3 -4,0 0,-14 4,14 7,-5 9,0 12,0 25,0"
              fill="none" stroke="{colors.stroke}" stroke-width="2.5"
              stroke-linecap="round" stroke-linejoin="round"/>
    <text y="112" text-anchor="middle" font-family="Inter, system-ui, sans-serif"
          font-size="16" font-weight="600" fill="{colors.brand_text}">cosalette</text>
  </g>

  <!-- MQTT Broker node -->
  <g transform="translate(695, 200)">
    <polygon points="0,-65 56.3,-32.5 56.3,32.5 0,65 -56.3,32.5 -56.3,-32.5"
             fill="none" stroke="{colors.stroke}" stroke-width="2" opacity="0.6"/>
    <polygon points="0,-50 43.3,-25 43.3,25 0,50 -43.3,25 -43.3,-25"
             fill="{colors.node_fill}" stroke="{colors.stroke}" stroke-width="1.5" opacity="0.9"/>
    <line x1="0" y1="-18" x2="0" y2="18" stroke="{colors.stroke}" stroke-width="2"/>
    <polyline points="-10,-8 0,-18 10,-8" fill="none" stroke="{colors.stroke}" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="-10,8 0,18 10,8" fill="none" stroke="{colors.stroke}" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"/>
    <text y="90" text-anchor="middle" font-family="Inter, system-ui, sans-serif"
          font-size="14" font-weight="500" fill="{colors.text}" opacity="0.82">MQTT Broker</text>
  </g>

  <!-- Telemetry arrows -->
  <line x1="170" y1="185" x2="310" y2="185"
        stroke="{colors.stroke}" stroke-width="1.8" opacity="0.7" marker-end="url(#arrow)"/>
  <line x1="490" y1="185" x2="630" y2="185"
        stroke="{colors.stroke}" stroke-width="1.8" opacity="0.7" marker-end="url(#arrow)"/>
  <!-- Command arrows -->
  <line x1="310" y1="215" x2="170" y2="215"
        stroke="{colors.command}" stroke-width="1.2" opacity="0.5" stroke-dasharray="6 4"
        marker-end="url(#arrow-cmd)"/>
  <line x1="630" y1="215" x2="490" y2="215"
        stroke="{colors.command}" stroke-width="1.2" opacity="0.5" stroke-dasharray="6 4"
        marker-end="url(#arrow-cmd)"/>

  <!-- Flow labels -->
  <text x="240" y="177" text-anchor="middle" font-family="Inter, system-ui, sans-serif"
        font-size="10" fill="{colors.text}" opacity="0.55">telemetry</text>
  <text x="240" y="233" text-anchor="middle" font-family="Inter, system-ui, sans-serif"
        font-size="10" fill="{colors.command}" opacity="0.45">commands</text>
  <text x="560" y="177" text-anchor="middle" font-family="Inter, system-ui, sans-serif"
        font-size="10" fill="{colors.text}" opacity="0.55">publish</text>
  <text x="560" y="233" text-anchor="middle" font-family="Inter, system-ui, sans-serif"
        font-size="10" fill="{colors.command}" opacity="0.45">subscribe</text>

  <!-- Waveform accents -->
  <g fill="none" stroke="{colors.stroke}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" opacity="0.055">
{accents}
  </g>
</svg>
"""


DOCS_HERO_SPECS: dict[str, list[tuple[str, DiagramColors]]] = {
    "docs": [
        ("docs-hero-dark", DOCS_HERO_DARK),
        ("docs-hero-light", DOCS_HERO_LIGHT),
    ],
}

ALL_GROUP_NAMES = list(GROUPS) + list(DOCS_HERO_SPECS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(groups: list[str]) -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    for group in groups:
        # Banner lockups (hero, social)
        for stem, spec in GROUPS.get(group, []):
            out = BRAND_DIR / f"{stem}.svg"
            out.write_text(generate_svg(spec), encoding="utf-8")
            print(f"OK    {out}  ({spec.width}\u00d7{spec.height})")
        # Docs hero diagrams
        for stem, colors in DOCS_HERO_SPECS.get(group, []):
            out = BRAND_DIR / f"{stem}.svg"
            out.write_text(generate_docs_hero(colors), encoding="utf-8")
            print(f"OK    {out}  (800\u00d7300)")


if __name__ == "__main__":
    requested = sys.argv[1:] or ALL_GROUP_NAMES
    unknown = [g for g in requested if g not in ALL_GROUP_NAMES]
    if unknown:
        sys.exit(
            f"Unknown group(s): {', '.join(unknown)}. Choose from: {', '.join(ALL_GROUP_NAMES)}"
        )
    main(requested)
