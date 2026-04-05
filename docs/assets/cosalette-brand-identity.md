# cosalette — Brand Identity

---

## 1. What is cosalette?

**One-liner:** An opinionated Python framework for building IoT-to-MQTT bridge
applications.

**Elevator pitch:** "FastAPI for MQTT daemons." Developers define devices (telemetry
pollers, command handlers), register hardware adapters, and the framework handles MQTT
wiring, structured logging, health reporting, error publishing, and graceful lifecycle
management.

### Core Concepts

| Concept                     | What it means                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------- |
| **Hexagonal architecture**  | Ports & adapters pattern — clean boundaries between business logic and infrastructure |
| **Device archetypes**       | Three first-class modes: telemetry, command, and long-running device coroutines       |
| **Orchestration lifecycle** | Four-phase lifecycle: configure → start → run → shutdown                              |
| **MQTT bridge**             | The framework is a *bridge* between hardware/protocols and an MQTT broker             |
| **Decorator-based API**     | `@app.telemetry()`, `@app.command()`, `@app.device()` — declarative, minimal code     |
| **Health & heartbeats**     | Continuous availability monitoring, LWT crash detection, per-device status            |
| **Signal filters**          | Rust-accelerated numeric filters (Pt1, Median, OneEuro) on telemetry streams          |

### Target Audience

- Python developers building home automation or industrial IoT integrations
- Hobbyist and professional IoT engineers
- People familiar with FastAPI, asyncio, and MQTT

### Brand Personality

| Attribute        | Description                                                        |
| ---------------- | ------------------------------------------------------------------ |
| **Precise**      | Engineering-grade, not playful                                     |
| **Industrial**   | Systems and infrastructure, not consumer gadgets                   |
| **Trustworthy**  | Reliable, observable, health-monitored                             |
| **Minimal**      | Low boilerplate, clean APIs — visuals should reflect that clarity  |
| **Warm**         | The amber/orange palette adds warmth to an otherwise technical tone|

---

## 2. Color Palette & Typography

### 2.1 Primary Colors

| Role          | Name        | Hex       | CSS Variable                   | Usage                        |
| ------------- | ----------- | --------- | ------------------------------ | ---------------------------- |
| **Primary**   | Amber       | `#FFC105` | `--md-primary-fg-color`        | Headers, nav, buttons, logo  |
| Primary Light | Amber Light | `#FFC929` | `--md-primary-fg-color--light` | Hover states, highlights     |
| Primary Dark  | Amber Dark  | `#FFA200` | `--md-primary-fg-color--dark`  | Active states, emphasis      |
| **Accent**    | Orange      | `#FF9100` | `--md-accent-fg-color`         | Links, interactive elements  |

### 2.2 Dark Theme Surface Colors (Slate Scheme)

| Role               | Approximate Hex | Description                     |
| ------------------ | --------------- | ------------------------------- |
| Background         | `#0D0D0F`       | Near-black, very low saturation |
| Background Subtle  | `#21222A`       | Cards, elevated surfaces        |
| Foreground         | `#E6E6E6` @82%  | Body text (slightly transparent)|
| Code Background    | `#1A1A1F`       | Code blocks                     |
| Footer             | `#1A1A1F` @87%  | Footer background               |

### 2.3 Light Theme

Uses Zensical's `default` scheme: white backgrounds, dark text, same amber/orange
primaries. Link color on light theme resolves to `#D19D00` (darker amber for contrast).
WCAG-safe accent for light backgrounds: `#b8860b` (dark goldenrod).

### 2.4 Extended Palette

| Role              | Hex       | Usage                                              |
| ----------------- | --------- | -------------------------------------------------- |
| Secondary Neutral | `#2C2C34` | Diagram backgrounds, card fills                    |
| Tertiary Neutral  | `#3A3A44` | Borders, dividers                                  |
| Success / Online  | `#2FB170` | Healthy status, success indicators                 |
| Error / Offline   | `#E6695B` | Error states, unhealthy indicators                 |
| Info / Structural | `#6791E0` | Informational cues, structural diagram elements    |

### 2.5 Typography

| Role      | Font           | Source       |
| --------- | -------------- | ------------ |
| UI Text   | Inter          | Google Fonts |
| Code      | JetBrains Mono | Google Fonts |

Typography is inherited from the Zensical theme. Brand assets (logos, hero graphics)
should complement Inter's geometric clarity.

---

## 3. Brand Assets

### Asset Overview

| # | Asset                     | Format(s)             | Primary Size             | Variants                 |
|---|---------------------------|-----------------------|--------------------------|--------------------------|
| 1 | **Logomark**              | SVG + PNG             | 512×512                  | Light bg, dark bg, mono  |
| 2 | **Logotype** (wordmark)   | SVG + PNG             | width ≤ 2000px           | Light bg, dark bg        |
| 3 | **Favicon**               | SVG + PNG + ICO       | 32×32, 16×16             | Single version           |
| 4 | **Docs header combo**     | SVG                   | height = 30–40px         | Light, dark scheme       |
| 5 | **README hero banner**    | SVG + PNG             | 1280×320                 | Light bg, dark bg        |
| 6 | **GitHub social preview** | SVG + PNG             | 1280×640                 | Single (dark only)       |
| 7 | **PyPI badge icon**       | SVG                   | 20×20                    | Amber, white             |
| 8 | **Docs hero illustration**| SVG + PNG             | 800×400                  | Light bg, dark bg        |

### 3.1 Logomark (Icon)

A standalone icon representing cosalette without text. Used as favicon (scaled down),
app icon, and alongside the wordmark.

**Design language:**

- Evokes **bridging**, **routing**, or **signal flow** — the core function of the
  framework (connecting devices to MQTT)
- Geometric, clean lines — matching Inter's precision
- Works at 16×16 (favicon) with no fine detail
- Amber (`#FFC105`) dominant fill on dark backgrounds
- Dark fill (`#0D0D0F` or `#1A1A1F`) on light backgrounds

**Visual metaphors** (blended in the current mark):

- **Hexagon** — nod to the hexagonal architecture
- **Signal pulse** — stylized waveform flowing through a conduit
- **Bridge / connector** — device on one side, MQTT broker on the other

**Anti-patterns:**

- No literal IoT device imagery (no light bulbs, thermometers, smart home icons)
- No cloud shapes (cosalette is a local bridge, not a cloud service)
- No gradients that collapse at small sizes
- No text inside the logomark

**Variants:**

| Variant               | Background    | Primary Fill     | File                        |
| --------------------- | ------------- | ---------------- | --------------------------- |
| Dark background       | Transparent   | `#FFC105` amber  | `logo-mark-dark.svg`        |
| Light background      | Transparent   | `#1A1A1F` dark   | `logo-mark-light.svg`       |
| Monochrome            | Transparent   | White            | `logo-mark-mono.svg`        |

Plus rasterized PNGs at 512×512, 256×256, 128×128, 64×64.

### 3.2 Logotype (Wordmark)

The word "cosalette" rendered in a consistent, recognizable style.

- Lowercase `cosalette` — the project always uses lowercase
- Based on or harmonizing with Inter (the docs font)
- Custom ECG wave ligature overlays the "tt" in the wordmark
- Amber (`#FFC105`) text on dark, dark text on light

**Variants:**

| Variant                      | Background  | Text Color | File                     |
| ---------------------------- | ----------- | ---------- | ------------------------ |
| Dark background              | Transparent | `#FFC105`  | `logotype-dark.svg`      |
| Light background             | Transparent | `#1A1A1F`  | `logotype-light.svg`     |
| Lockup (icon + text) dark    | Transparent | Mixed      | `logo-lockup-dark.svg`   |
| Lockup (icon + text) light   | Transparent | Mixed      | `logo-lockup-light.svg`  |

### 3.3 Favicon

Derived from the logomark, simplified for legibility at very small sizes.

- Reads clearly at 16×16 and 32×32 pixels
- Single-color amber on transparent
- No interior detail that disappears at 16px

**Files:** `favicon.svg`, `favicon-32.png`, `favicon-16.png`, `favicon.ico`

### 3.4 Docs Header Combo

The logomark + wordmark lockup displayed in the top-left corner of the documentation
site, constrained to ~30–40px height.

- Total height: 30–40px (Zensical header constraint)
- Horizontal: logomark + gap + wordmark, total width ≤ 200px
- Works on both slate (dark) and default (light) schemes
- SVG format (Zensical renders it inline)

**Configuration:** The `logo` key under `[project.theme]` in `zensical.toml` points
to the lockup SVG. The `brand-logo.css` stylesheet handles dark/light scheme switching.

### 3.5 README Hero Banner

**Dimensions:** 1280×320 px (4:1 aspect ratio)

**Variants:** Dark background (`hero-banner-dark`) and light background
(`hero-banner-light`), both SVG + PNG.

**Design:**

- **Background pattern:** Honeycomb tessellation (see §6 for geometry details)
- **Waveform accents:** 5 ECG pulse polylines placed at hex-center lattice positions,
  each ~60 px wide × ~30 px tall
- **Lockup:** Logomark + wordmark centered. Logomark at scale 0.25 (512 → ~128 px).
  Wordmark via nested `<svg>` with `viewBox="0 0 338 100"` at 540×160.
  ECG wave ligature overlays the "tt" (stroke-width 5.8)
- **Tagline:** "An opinionated Python framework for IoT-to-MQTT bridges" at
  font-size 20, Inter, centered, opacity 0.82
- **Dark variant:** Amber `#FFC105` on dark `#0D0D0F`, text `#E6E6E6`
- **Light variant:** Dark `#1A1A1F` on white `#FFFFFF`, text `#1A1A1F`

**Embedding in README.md** (uses `<picture>` for dark/light mode):

```html
<picture>
  <source media="(prefers-color-scheme: dark)"
          srcset="docs/assets/images/brand/hero-banner-dark.png">
  <source media="(prefers-color-scheme: light)"
          srcset="docs/assets/images/brand/hero-banner-light.png">
  <img alt="cosalette — An opinionated Python framework for IoT-to-MQTT bridges"
       src="docs/assets/images/brand/hero-banner-dark.png"
       style="max-width: 100%; height: auto;">
</picture>
```

**Files:** `hero-banner-dark.svg` / `.png`, `hero-banner-light.svg` / `.png`

### 3.6 GitHub Social Preview (Open Graph Image)

**Dimensions:** 1280×640 px (2:1 aspect ratio). Dark only (amber on `#0D0D0F`).

**Design:**

- Same honeycomb + waveform accent system as hero banners, with 7 waveform accents
  spread across the taller canvas
- Lockup scaled up: logomark at scale 0.386, wordmark at 834×247
- Tagline: "A Python framework for IoT-to-MQTT bridges" (shorter than hero), font-size
  36, centered below lockup

**Usage:**

- GitHub: Repository → Settings → Social preview → upload `social-preview.png`
- Docs: Open Graph `<meta>` tags in `overrides/main.html` via the `extrahead` block

**Files:** `social-preview.svg` / `.png`

### 3.7 PyPI Badge Icon

A tiny version of the logomark for shields.io badges or custom logo badges.

- 20×20 px effective size
- Single color, extreme simplicity
- Recognizable as a silhouette

**Files:** `badge-icon.svg` (amber), `badge-icon-white.svg` (white for shields.io)

### 3.8 Docs Hero Illustration

**Dimensions:** 800×400 px (2:1 aspect ratio, scalable SVG)

**Design:**

- System diagram showing Device → cosalette → MQTT Broker data flow
- Three hexagonal nodes with inner icons: sensor symbol (left), logomark with ECG
  pulse (center), pub/sub arrows (right)
- Bidirectional arrows: solid amber for data flow (telemetry, publish), dashed orange
  for commands (commands, subscribe)
- Honeycomb background pattern at lower opacity (0.04) for consistency with banners
- Four subtle waveform accents in corners
- Center node is larger (90 px hex radius) to emphasize cosalette as the hub
- "cosalette" label in amber below center node

**Embedded in `docs/index.md`** between the tagline and "What is cosalette?" section
using a `<figure>` with separate dark/light images selected via `#only-dark` and
`#only-light`.

**Files:** `docs-hero-dark.svg` / `.png`, `docs-hero-light.svg` / `.png`

---

## 4. Visual Language Guidelines

### 4.1 Icon Style

The documentation uses Material Design and Octicons icon shortcodes. Custom
illustrations and the logo follow a **consistent geometric line style:**

- Stroke weight: 2px (matching Lucide/Material icon conventions)
- Corner radius: slight rounding (2–4px) — not sharp, not fully rounded
- Filled shapes use flat color, no gradients
- Shadows: none (flat design)

### 4.2 Diagram Colors

Mermaid diagrams use CSS variable overrides defined in `mermaid-brand.css`. The
following palette applies to all diagrams:

| Diagram Element        | Recommended Color | Hex       |
| ---------------------- | ----------------- | --------- |
| Primary nodes/boxes    | Amber             | `#FFC105` |
| Connection lines       | Orange accent     | `#FF9100` |
| Background fills       | Dark slate        | `#2C2C34` |
| Success / healthy      | Green             | `#2FB170` |
| Error / unhealthy      | Red               | `#E6695B` |
| Info / structural      | Blue              | `#6791E0` |
| Text on dark fill      | White @82%        | `#D1D1D1` |
| Text on amber fill     | Dark              | `#0D0D0F` |

### 4.3 Spacing & Layout

- Logo lockup: logomark height = wordmark cap height, 8px gap between them
- Minimum clear space around logomark: 1× the logomark width on all sides
- Hero banners: content centered both axes, generous padding (≥ 40px from edges)

### 4.4 Do / Don't

| Do                                              | Don't                                             |
| ------------------------------------------------ | ------------------------------------------------- |
| Use amber as the dominant brand color            | Use amber as a background fill for large areas     |
| Keep the dark slate background as the "home" bg  | Use pure black `#000000` — use `#0D0D0F` instead  |
| Let the logomark breathe with clear space        | Crowd the logo with text or other elements         |
| Use geometric, technical illustration style      | Use gradients, 3D effects, or drop shadows         |
| Convey bridging, routing, signals, orchestration | Show literal hardware devices, smart home gadgets  |
| Reflect precision and reliability                | Be playful, whimsical, or cartoon-like             |

---

## 5. Asset Inventory

All brand assets are stored in `docs/assets/images/brand/`:

```
docs/assets/images/brand/
├── logo-mark-dark.svg
├── logo-mark-light.svg
├── logo-mark-mono.svg
├── logo-mark-512.png
├── logo-mark-256.png
├── logo-mark-128.png
├── logo-mark-64.png
├── logo-32-dark.svg            (favicon-size logomark)
├── logo-32-light.svg
├── logotype-dark.svg
├── logotype-light.svg
├── logo-lockup-dark.svg
├── logo-lockup-light.svg
├── favicon.svg
├── favicon-32.png
├── favicon-16.png
├── favicon.ico
├── hero-banner-dark.svg
├── hero-banner-dark.png
├── hero-banner-light.svg
├── hero-banner-light.png
├── social-preview.svg
├── social-preview.png
├── badge-icon.svg
├── badge-icon-white.svg
├── docs-hero-dark.svg
├── docs-hero-dark.png
├── docs-hero-light.svg
└── docs-hero-light.png
```

### Integration Points

| Surface                    | File / Config                             | Mechanism                                    |
| -------------------------- | ----------------------------------------- | -------------------------------------------- |
| Docs header logo           | `zensical.toml` → `[project.theme].logo`  | Lockup SVG, scheme switch via `brand-logo.css`|
| Favicon                    | `zensical.toml` → favicon config          | SVG + ICO in `docs/assets/images/brand/`      |
| README hero                | `README.md`                               | `<picture>` element (dark/light mode)         |
| GitHub social preview      | GitHub repo settings                      | Upload `social-preview.png`                   |
| Open Graph metadata        | `overrides/main.html`                     | `extrahead` block with `<meta>` tags          |
| Docs homepage hero         | `docs/index.md`                           | `<figure>` with `#only-dark` / `#only-light` images |
| 404 page                   | `overrides/404.html`                      | Branded hex + heartbeat motif                 |
| Hero responsive styling    | `brand-hero.css`                          | `max-width: 100%; height: auto`               |
| Mermaid diagram colors     | `mermaid-brand.css`                       | `--md-mermaid-*` CSS variable overrides       |
| Theme overrides directory  | `zensical.toml` → `custom_dir`            | `overrides/`                                  |

---

## 6. Honeycomb Pattern System

The hero banners and social preview share a common background pattern system. These
parameters are encoded in `scripts/generate_brand_svgs.py` and should be updated there,
not by editing SVGs directly.

### Honeycomb Tessellation

- **Geometry:** Flat-top hexagons, side length s = 40 px
- **Tile dimensions:** 120 × 69.282 px (3s × s√3)
- **Tile composition:** One hexagonal polygon + two horizontal edge stubs connecting
  to adjacent tiles (shared edges)
- **Stroke width:** 1.6 px
- **Pattern opacity:** 0.055 (very subtle, background texture only)

### Hex-Center Lattice (Waveform Accent Positions)

Waveform accents are placed on the hex-center lattice — the grid of hexagon centers:

- **Even rows** (m = 0, 1, 2, …): y = 69.282 × m, x = 0, 120, 240, …
- **Odd rows** (m = 0, 1, 2, …): y = 34.641 + 69.282 × m, x = 60, 180, 300, …

Each accent is an ECG pulse polyline (~60 px wide × ~30 px tall), using relative
offsets from the center point. Accents use the same stroke color and opacity as the
honeycomb pattern.

### cairosvg Compatibility

**Important:** Waveform accents must be inlined as direct `<polyline>` elements. Using
`<symbol>` / `<use>` elements causes cairosvg to render incorrect colors (wrong stroke
color applied). This is a known cairosvg limitation and the reason all accents are
generated inline by the generator script.

---

## 7. Asset Generation Tooling

Brand assets are generated programmatically. The scripts are the **source of truth** —
edit design tokens there, not the SVG files directly.

### Generator Script

**`scripts/generate_brand_svgs.py`** — parameterized SVG generator

- Encodes all design decisions: colors, sizes, positions, honeycomb geometry
- Uses `BannerSpec` dataclass with all layout parameters
- Factory functions: `hero_dark()`, `hero_light()`, `social_preview()`
- CLI: `uv run scripts/generate_brand_svgs.py [hero|social]`

### Rasterizer Script

**`scripts/render_brand_assets.py`** — SVG-to-PNG rasterizer via cairosvg

- Renders generated SVGs to PNG at exact pixel dimensions
- CLI: `uv run scripts/render_brand_assets.py [hero|social]`
- Requires `cairosvg` (declared in `pyproject.toml` dev dependency group)

### Workflow

```bash
# 1. Edit design tokens in generate_brand_svgs.py
# 2. Regenerate SVGs
uv run scripts/generate_brand_svgs.py

# 3. Rasterize to PNG
uv run scripts/render_brand_assets.py

# 4. Verify visually, commit both SVG and PNG
```
