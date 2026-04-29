# Plan: Documentation Improvements — Font Size, ADR Links, Code Annotations

**Branch:** `docs/font-adr-annotations`
**Status:** Shipped — this doc reflects the final implementation.

---

## Overview

Three targeted documentation improvements across the Zensical docs site:

1. Reduce global font size by ~10%
2. Auto-link all ADR references in the built HTML (post-build script)
3. Add `# (N)!` parameter annotations to `@app.periodic` code examples

---

## Change 1 — Font Size Reduction (~10%)

**Why:** User always views at 90% browser zoom; baking this in as the default makes the
reading experience native without manual zoom adjustments.

### Approach: New CSS file `docs/assets/stylesheets/typography.css`

Material for MkDocs sets `html { font-size }` at three responsive breakpoints. The
current values and their 90% equivalents:

| Breakpoint | Current | 90% reduced |
|---|---|---|
| Default | `125%` | `112.5%` |
| `≥100em` | `137.5%` | `123.75%` |
| `≥125em` | `150%` | `135%` |

Overriding these three rules in a custom CSS file (loaded **after** the theme via
`extra_css`) is the standard Material for MkDocs customization pattern.

**Why a new file vs. editing an existing stylesheet:**
- The existing CSS files are all feature-specific (hero, brand, cards). Typography is
  a cross-cutting concern with its own file.
- Easier to revert by removing from `extra_css` in `zensical.toml`.

**Files changed:**
- `docs/assets/stylesheets/typography.css` — NEW
- `zensical.toml` — prepend `"assets/stylesheets/typography.css"` to `extra_css`

---

## Change 2 — Auto-link ADR References (post-build script)

**Original plan:** Manually patch two specific bare references in `api.md` and
`errors.md`. **Shipped as:** A post-build script (`docs/postprocess.py`) that rewrites
all bare `ADR-NNN` text in the generated HTML site automatically — covering both
hand-written prose and auto-generated docstring output.

**Why the approach changed:** MkDocs hooks are not supported by the Zensical build
engine. A post-build HTML rewriting pass covers all pages at once without per-file
manual edits, and is future-proof for new ADR references.

**What ships:**
- `docs/postprocess.py` — new, walks `site/*.html`, rewrites bare ADR-NNN → hyperlinks,
  skips existing links, code, pre, title, and head regions.
- `Taskfile.yml` — `docs:build` task now runs `uv run docs/postprocess.py` after build.

**Files not changed:** `docs/reference/errors.md` (covered by the script).

---

## Change 3 — Parameter Annotations in `@app.periodic` Code Example

**Why:** The `@app.periodic` section had a parameter table that duplicated the
docstring Args section verbatim. The table was removed and replaced with annotated code
examples using `# (N)!` notation — showing all four `interval=` forms and the
`enabled=` callable pattern.

**What ships:**
- Duplicate parameter table removed from `docs/reference/api.md`.
- Five annotated `@app.periodic` examples added, covering:
  1. `interval` as `float`
  2. `interval` as `datetime.timedelta`
  3. `enabled` as a callable (ADR-038 pattern)
  4. `interval` as `SettingRef`
  5. `interval` as `Callable[[Settings], float]`

**Files changed:**
- `docs/reference/api.md`

---

## Summary

| Change | Files | Effort |
|---|---|---|
| Font size (-10%) | 2 (`typography.css` new + `zensical.toml`) | Small |
| ADR auto-linking (post-build) | 2 (`postprocess.py` new + `Taskfile.yml`) | Medium |
| `@app.periodic` annotations (table removed) | 1 (`api.md`) | Small |

**Total:** 3 files modified, 2 files created.
