# Plan: Documentation Improvements — Font Size, ADR Links, Code Annotations

**Branch:** `docs/font-adr-annotations`

---

## Overview

Three targeted documentation improvements across the Zensical docs site:

1. Reduce global font size by ~10%
2. Link all unlinked ADR references in reference pages
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

## Change 2 — Link Unlinked ADR References

**Why:** ADR numbers appear in prose without hyperlinks, breaking navigation to design
rationale. Two occurrences found across the reference pages:

| File | Line | Current text | Corrected link |
|---|---|---|---|
| `docs/reference/api.md` | 67 | `(ADR-038 pattern)` | `([ADR-038](../adr/ADR-038-deferred-enabled-for-decorator-registrations.md) pattern)` |
| `docs/reference/errors.md` | 233 | `see ADR-019` | `see [ADR-019](../adr/ADR-019-scoped-name-uniqueness.md)` |

Both ADR files exist at their expected paths.

**Files changed:**
- `docs/reference/api.md`
- `docs/reference/errors.md`

---

## Change 3 — Parameter Annotations in `@app.periodic` Code Example

**Why:** The `App.periodic(...)` section already has a full parameter table, but the
code example is unannotated. Adding `# (N)!` annotations creates a second, in-context
explanation directly where the parameters appear — matching the style used in
`concepts/error-handling.md` and `getting-started/quickstart.md`.

**Approach:** Annotate the single existing code block (which has three `@app.periodic`
calls) to show the key parameters in context. The parameter table is unchanged.

**Planned annotations:**

```python
@app.periodic("flush-buffer", interval=30.0)  # (1)!
async def flush_buffer(cache: BufferCache) -> None: ...

@app.periodic(
    "watchdog",                                  # (2)!
    interval=datetime.timedelta(minutes=1),      # (3)!
    enabled=lambda s: s.watchdog_enabled,        # (4)!
)
async def watchdog_ping(settings: AppSettings) -> None: ...

@app.periodic("led-sync", interval=SettingRef("led_interval"))  # (5)!
async def led_sync(led: LedPort) -> None: ...
```

Numbered explanations below the block:

1. Minimal form — positional `name` + keyword `interval` as plain `float` (seconds). All other params default.
2. `name` — unique task identifier string; defaults to `func.__name__` when omitted.
3. `interval` — also accepts `datetime.timedelta`, `Callable[[Settings], float]`, or `SettingRef`; always resolved at bootstrap.
4. `enabled` — callable is evaluated at bootstrap with the resolved `Settings` instance; `False` skips registration silently. ([ADR-038](../adr/ADR-038-deferred-enabled-for-decorator-registrations.md))
5. `SettingRef("led_interval")` — deferred interval: the value of `AppSettings.led_interval` is read from settings at bootstrap, not at import time.

**Files changed:**
- `docs/reference/api.md`

---

## Summary

| Change | Files | Effort |
|---|---|---|
| Font size (-10%) | 2 (`typography.css` new + `zensical.toml`) | Small |
| Link ADR-038, ADR-019 | 2 (`api.md`, `errors.md`) | Trivial |
| `@app.periodic` annotations | 1 (`api.md`) | Small |

**Total:** 3 files modified, 1 file created.

---

## Next Steps

1. Create feature branch `docs/font-adr-annotations` from `main`
2. Create `typography.css`
3. Update `zensical.toml`
4. Patch `api.md` (ADR-038 link + annotations)
5. Patch `errors.md` (ADR-019 link)
6. Pre-PR gate + PR
