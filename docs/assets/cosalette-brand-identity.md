# cosalette — Brand Identity Brief

---

## 1. What is cosalette?

**One-liner:** An opinionated Python framework for building IoT-to-MQTT bridge
applications.

**Elevator pitch:** "FastAPI for MQTT daemons." Developers define devices (telemetry
pollers, command handlers), register hardware adapters, and the framework handles MQTT
wiring, structured logging, health reporting, error publishing, and graceful lifecycle
management.

### Core Concepts (Visual Metaphor Sources)

| Concept                     | What it means                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------- |
| **Hexagonal architecture**  | Ports & adapters pattern — clean boundaries between business logic and infrastructure |
| **Device archetypes**       | Three first-class modes: telemetry, command, and long-running device coroutines        |
| **Orchestration lifecycle** | Four-phase lifecycle: configure → start → run → shutdown                              |
| **MQTT bridge**             | The framework is a *bridge* between hardware/protocols and an MQTT broker             |
| **Decorator-based API**     | `@app.telemetry()`, `@app.command()`, `@app.device()` — declarative, minimal code    |
| **Health & heartbeats**     | Continuous availability monitoring, LWT crash detection, per-device status            |
| **Signal filters**          | Rust-accelerated numeric filters (Pt1, Median, OneEuro) on telemetry streams         |

### Target Audience

- Python developers building home automation or industrial IoT integrations
- Hobbyist and professional IoT engineers
- People familiar with FastAPI, asyncio, and MQTT

### Brand Personality

| Attribute        | Description                                                       |
| ---------------- | ----------------------------------------------------------------- |
| **Precise**      | Engineering-grade, not playful                                    |
| **Industrial**   | Systems and infrastructure, not consumer gadgets                  |
| **Trustworthy**  | Reliable, observable, health-monitored                            |
| **Minimal**      | Low boilerplate, clean APIs — visuals should reflect that clarity |
| **Warm**         | The amber/orange palette adds warmth to an otherwise technical tone|

---

## 2. Current Visual Identity

### 2.1 Color Palette (Preserve & Extend)

The framework's documentation already uses a defined palette. **These colors are
canonical and must remain the brand center.** New assets should use these as the
primary palette and may introduce supporting neutrals and a semantic accent.

#### Primary Colors

| Role          | Name        | Hex       | CSS Variable                   | Usage                        |
| ------------- | ----------- | --------- | ------------------------------ | ---------------------------- |
| **Primary**   | Amber       | `#FFC105` | `--md-primary-fg-color`        | Headers, nav, buttons, logo  |
| Primary Light | Amber Light | `#FFC929` | `--md-primary-fg-color--light` | Hover states, highlights     |
| Primary Dark  | Amber Dark  | `#FFA200` | `--md-primary-fg-color--dark`  | Active states, emphasis      |
| **Accent**    | Orange      | `#FF9100` | `--md-accent-fg-color`         | Links, interactive elements  |

#### Dark Theme Surface Colors (Slate Scheme)

| Role               | Approximate Hex | Description                     |
| ------------------ | --------------- | ------------------------------- |
| Background         | `#0D0D0F`       | Near-black, very low saturation |
| Background Subtle  | `#21222A`       | Cards, elevated surfaces        |
| Foreground         | `#E6E6E6` @82%  | Body text (slightly transparent)|
| Code Background    | `#1A1A1F`       | Code blocks                     |
| Footer             | `#1A1A1F` @87%  | Footer background               |

#### Light Theme

Uses Zensical's `default` scheme: white backgrounds, dark text, same amber/orange
primaries. Link color on light theme resolves to `#D19D00` (darker amber for contrast).

#### Proposed Extended Palette

These are **suggestions for the brand designer** to validate and refine:

| Role              | Suggested Hex | Rationale                                                    |
| ----------------- | ------------- | ------------------------------------------------------------ |
| Secondary Neutral | `#2C2C34`     | Dark slate for diagram backgrounds, card fills               |
| Tertiary Neutral  | `#3A3A44`     | Lighter slate for borders, dividers                          |
| Success / Online  | `#2FB170`     | Already used for code string highlighting; reuse for "healthy" |
| Error / Offline   | `#E6695B`     | Already used for code number highlighting; reuse for "error" |
| Info / Structural | `#6791E0`     | Already used for code keywords; reuse for informational cues |

### 2.2 Typography

| Role      | Font           | Source       |
| --------- | -------------- | ------------ |
| UI Text   | Inter          | Google Fonts |
| Code      | JetBrains Mono | Google Fonts |

Typography is inherited from the Zensical theme and should not change. Brand assets
(logos, hero graphics) should complement Inter's geometric clarity.

### 2.3 Current Defaults (To Be Replaced)

| Surface                | Current State                              | Action                           |
| ---------------------- | ------------------------------------------ | -------------------------------- |
| Docs header logo       | Generic Lucide "book-open" SVG             | **Replace** with custom logomark |
| Favicon                | Generic Zensical book icon (PNG)           | **Replace** with custom favicon  |
| README hero            | None — text only                           | **Add** hero banner              |
| GitHub social preview  | None — GitHub auto-generates from README   | **Add** OG image                 |
| Open Graph metadata    | Missing from docs site                     | **Add** after image exists       |
| Docs homepage hero     | Text + code sample, no illustration        | **Add** hero graphic (optional)  |
| 404 page               | Default Zensical 404                       | **Replace** with custom version  |
| Section landing icons  | Material Design icon shortcodes            | **Keep** — these work well       |

---

## 3. Asset Specifications

### Asset Overview

| # | Asset                     | Priority | Format(s)             | Primary Size             | Variants Needed          |
|---|---------------------------|----------|-----------------------|--------------------------|--------------------------|
| 1 | **Logomark**              | P0       | SVG + PNG             | 512×512                  | Light bg, dark bg, mono  |
| 2 | **Logotype** (wordmark)   | P0       | SVG + PNG             | width ≤ 2000px           | Light bg, dark bg        |
| 3 | **Favicon**               | P0       | PNG + ICO             | 32×32, 16×16             | Single version           |
| 4 | **Docs header combo**     | P0       | SVG                   | height = 30–40px         | Light, dark scheme       |
| 5 | **README hero banner**    | P1       | PNG (+ SVG if vector) | 1280×320                 | Light bg, dark bg        |
| 6 | **GitHub social preview** | P1       | PNG                   | 1280×640 (40 pt border)* | Single version           |
| 7 | **PyPI badge icon**       | P2       | SVG                   | 20×20                    | Single version           |
| 8 | **Docs hero illustration**| P2       | SVG or PNG            | 800×400 (flexible)       | Dark bg preferred        |

*) Template available: https://github.com/ff-fab/cosalette/settings/og-template

### 3.1 Logomark (Icon)

**What it is:** A standalone icon that represents cosalette without text. Used as
favicon (scaled down), app icon, and alongside the wordmark.

**Design direction:**

- Should evoke **bridging**, **routing**, or **signal flow** — the core function of
  the framework (connecting devices to MQTT)
- Geometric, clean lines — matching Inter's precision
- Should work at 16×16 (favicon) so avoid fine detail
- Amber (`#FFC105`) as the dominant fill on dark backgrounds
- Dark fill (`#0D0D0F` or `#1A1A1F`) on light backgrounds

**Visual metaphor candidates** (pick one or blend):

1. **Bridge / connector:** Two nodes connected by a signal path — device on one side,
   MQTT broker on the other
2. **Hexagon:** Nod to the hexagonal architecture — a hexagonal frame with signal
   lines flowing through it
3. **Signal pulse:** A stylized waveform or pulse flowing through a conduit — nods to
   telemetry and signal filters
4. **Port / adapter plug:** Abstract interlocking shapes suggesting protocol
   interoperability

**Anti-patterns:**

- No literal IoT device imagery (no light bulbs, thermometers, smart home icons)
- No cloud shapes (cosalette is a local bridge, not a cloud service)
- No gradients that collapse at small sizes
- No text inside the logomark

**Deliverables:**

| Variant               | Background    | Primary Fill     | File                        |
| --------------------- | ------------- | ---------------- | --------------------------- |
| Dark background       | Transparent   | `#FFC105` amber  | `logo-mark-dark.svg`        |
| Light background      | Transparent   | `#1A1A1F` dark   | `logo-mark-light.svg`       |
| Monochrome            | Transparent   | White            | `logo-mark-mono.svg`        |

Plus rasterized PNGs at 512×512, 256×256, 128×128, 64×64.

### 3.2 Logotype (Wordmark)

**What it is:** The word "cosalette" rendered in a consistent, recognizable style.

**Design direction:**

- Lowercase `cosalette` — the project always uses lowercase
- Based on or harmonizing with Inter (the docs font)
- May use a custom ligature or stylistic treatment on the "co" or "ette" portions
- Amber (`#FFC105`) text on dark, dark text on light
- Optional: integrate the logomark to the left of the wordmark for a lockup

**Deliverables:**

| Variant         | Background  | Text Color     | File                          |
| --------------- | ----------- | -------------- | ----------------------------- |
| Dark background | Transparent | `#FFC105`      | `logotype-dark.svg`           |
| Light background| Transparent | `#1A1A1F`      | `logotype-light.svg`          |
| Lockup (icon+text) dark | Transparent | Mixed  | `logo-lockup-dark.svg`        |
| Lockup (icon+text) light| Transparent | Mixed  | `logo-lockup-light.svg`       |

### 3.3 Favicon

**What it is:** The browser tab icon. Derived from the logomark, simplified for
legibility at very small sizes.

**Constraints:**

- Must read clearly at 16×16 and 32×32 pixels
- Single-color amber on transparent is fine
- Drop any interior detail that disappears at 16px

**Deliverables:**

- `favicon-32.png` (32×32)
- `favicon-16.png` (16×16)
- `favicon.ico` (multi-resolution ICO bundle)
- `favicon.svg` (vector for modern browsers)

### 3.4 Docs Header Combo

**What it is:** The top-left corner of the documentation site. Currently shows a
generic book SVG + the text "cosalette". Will be replaced with the logomark + wordmark
lockup, constrained to ~30–40px height.

**Constraints:**

- Total height: 30–40px (Zensical header constraint)
- Horizontal: logomark + gap + wordmark, total width ≤ 200px
- Must look good on both slate (dark) and default (light) schemes
- SVG format required (Zensical renders it inline)

**How to embed:** The Zensical config in `zensical.toml` will need a `logo` key
under `[project.theme]` pointing to the SVG file path, or a custom logo override
depending on the version.

### 3.5 README Hero Banner

**What it is:** A wide banner image at the top of `README.md`, above the badge row.
This is the first thing visitors see on the GitHub repository page.

**Design direction:**

- 1280×320 px (4:1 aspect ratio) — standard GitHub README width
- Contains: logomark + wordmark lockup, tagline ("An opinionated Python framework for
  IoT-to-MQTT bridges"), and subtle background pattern
- Background pattern should evoke signal flow, network topology, or hexagonal grid —
  **not** a photograph, **not** clip art
- Dark version primary (GitHub dark mode is increasingly common)
- Optional: light version for `#gh-light-mode-only` / `#gh-dark-mode-only` suffix
  trick in GitHub markdown

**Embedding in README.md:**

```markdown
<!-- For dual-mode support -->
<p align="center">
  <img src="docs/assets/images/hero-banner-dark.png#gh-dark-mode-only" alt="cosalette" width="100%">
  <img src="docs/assets/images/hero-banner-light.png#gh-light-mode-only" alt="cosalette" width="100%">
</p>
```

**Deliverables:**

- `hero-banner-dark.png` (1280×320, dark bg, amber accents)
- `hero-banner-light.png` (1280×320, light bg, dark accents)

### 3.6 GitHub Social Preview (Open Graph Image)

**What it is:** The image shown when the GitHub repo URL is shared on Twitter/X,
Slack, Discord, LinkedIn, etc. Also used by docs site if OG meta tags are added.

**Design direction:**

- 1280×640 px (2:1 aspect ratio) — GitHub's recommended social preview size
- Contains: logomark + wordmark, tagline, and a visual that conveys the framework's
  purpose
- Should work well when cropped to ~600×315 (Twitter card crop)
- Consider adding a subtle code snippet or architectural diagram motif in the
  background (not readable, just texture)
- Amber/orange palette on dark background preferred

**How to set:**

- GitHub: Repository → Settings → Social preview → Upload image
- Docs: Add `<meta property="og:image" ...>` to the site, either via Zensical config
  (`social` plugin or `extra` metadata) or a custom `overrides/main.html`

**Deliverables:**

- `social-preview.png` (1280×640)

### 3.7 PyPI Badge Icon (Low Priority)

**What it is:** A tiny version of the logomark that could replace the default PyPI
logo in a shields.io badge, or be used as a custom logo badge.

**Constraints:**

- 20×20 px effective size
- Single color, extreme simplicity
- Must be recognizable as a silhouette

**Deliverables:**

- `badge-icon.svg` (20×20 viewBox)

### 3.8 Docs Hero Illustration (Optional)

**What it is:** An illustration on the docs homepage (`docs/index.md`) that
communicates the framework's purpose visually. Appears between the tagline and code
sample, or alongside the code sample.

**Design direction:**

- System diagram aesthetic: boxes, arrows, signal paths
- Stylized representation of: Device → cosalette → MQTT Broker
- Use the extended palette: amber nodes, orange connections, slate backgrounds,
  success-green for "healthy" state indicators
- This is an **illustration**, not a screenshot or photograph
- Should complement the existing Mermaid diagrams in the docs, not compete with them

**Deliverables:**

- `docs-hero.svg` (vector, ~800×400, scalable)
- `docs-hero.png` (rasterized fallback)

---

## 4. Visual Language Guidelines

### 4.1 Icon Style

The documentation currently uses Material Design and Octicons icon shortcodes. These
should remain as-is. Custom illustrations and the logo should follow a **consistent
geometric line style:**

- Stroke weight: 2px (matching Lucide/Material icon conventions)
- Corner radius: slight rounding (2–4px) — not sharp, not fully rounded
- Filled shapes use flat color, no gradients
- Shadows: none (flat design)

### 4.2 Diagram Colors

The existing Mermaid diagrams in the docs use inline fill/stroke colors. For
consistency, diagrams should adopt the brand palette when possible:

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

## 5. File Organization

All brand assets should be stored in the repository under a single directory:

```
docs/assets/images/brand/
├── logo-mark-dark.svg
├── logo-mark-light.svg
├── logo-mark-mono.svg
├── logo-mark-512.png
├── logo-mark-256.png
├── logo-mark-128.png
├── logo-mark-64.png
├── logotype-dark.svg
├── logotype-light.svg
├── logo-lockup-dark.svg
├── logo-lockup-light.svg
├── favicon.svg
├── favicon-32.png
├── favicon-16.png
├── favicon.ico
├── hero-banner-dark.png
├── hero-banner-light.png
├── social-preview.png
├── badge-icon.svg
└── docs-hero.svg
```

### Integration Checklist

After assets are created, the following files need updates:

| File / Surface                 | Change                                                              |
| ------------------------------ | ------------------------------------------------------------------- |
| `zensical.toml`                | Add `logo` key under `[project.theme]` pointing to lockup SVG      |
| `zensical.toml`                | Add favicon config if supported, or place in `docs/assets/images/`  |
| `docs/index.md`                | Add hero illustration between tagline and "Quick Example"           |
| `README.md`                    | Add hero banner at top, before badge row                            |
| GitHub repo settings           | Upload `social-preview.png` as social preview image                 |
| `zensical.toml` or overrides   | Add Open Graph `<meta>` tags for social sharing                     |
| `docs/stylesheets/brand.css`   | Optional: custom CSS for hero illustration sizing/positioning       |
| Mermaid diagrams (conceptually)| Gradually align fill/stroke colors with brand palette               |

---

## 6. Summary for the Image Generator

> **You are designing a brand identity for cosalette, an open-source Python framework
> for IoT-to-MQTT bridge applications. The brand should feel technical, precise, and
> industrial — like developer tooling, not consumer electronics. The core palette is
> amber (#FFC105) primary and orange (#FF9100) accent on dark slate backgrounds
> (#0D0D0F). The visual language should evoke bridging, signal routing, hexagonal
> architecture, and orchestration. Use geometric line art, flat colors, no gradients or
> 3D effects. The typography companion is Inter (geometric sans-serif). All assets must
> work at both small (favicon, 16px) and large (banner, 1280px) scales.**
>
> Please generate the following assets in priority order:
>
> 1. **Logomark** — a standalone icon (~512×512 SVG) representing bridging/signal flow
> 2. **Logotype** — the word "cosalette" in lowercase, stylized
> 3. **Favicon** — simplified logomark at 32×32 and 16×16
> 4. **Docs header lockup** — icon + wordmark, 30–40px height
> 5. **README hero banner** — 1280×320, dark and light versions
> 6. **GitHub social preview** — 1280×640, shareable OG image
> 7. **Docs hero illustration** — ~800×400, stylized system diagram
>
> Each asset needs dark-background and light-background variants where noted.
> Use the exact hex values specified in this brief.
