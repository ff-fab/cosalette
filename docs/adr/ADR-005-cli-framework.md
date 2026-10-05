---
status: Accepted
date: 2026-02-14
impact: moderate
tags: [cli]
---

# ADR-005: CLI Framework

## Status

Accepted **Date:** 2026-02-14 | Amended **Date:** 2026-10-05

## Context

Every cosalette application needs a consistent CLI providing at minimum:
`--dry-run`, `--version`, `--log-level`, `--log-format`, and `--env-file`. The framework
should provide this CLI scaffolding so that all projects behave identically from the
command line without project authors reimplementing argument parsing.

The CLI choice should align with cosalette's type-hint-driven philosophy — the framework
already uses pydantic for configuration (type hints → validated settings) and PEP 544
Protocols for ports (type hints → interface contracts). The CLI framework should
continue this pattern: type hints → argument parsing.

## Decision

Use **Typer** for CLI scaffolding because its type-hint-driven argument parsing aligns
with cosalette's pydantic-settings approach (type-driven configuration everywhere), and
Click is pulled in transitively as a dependency anyway.

The framework provides CLI options via Typer, and `app.run()` handles everything:

```bash
$ velux2mqtt --help
Usage: velux2mqtt [OPTIONS]

  velux2mqtt v0.1.0 — IoT-to-MQTT bridge powered by cosalette

Options:
  --version              Show version and exit.
  --dry-run              Use dry-run adapters (no real hardware).
  --log-level TEXT       Override log level (DEBUG, INFO, WARNING, ERROR).
  --log-format TEXT      Override log format (json, text).
  --env-file PATH        Path to .env file (default: .env).
  --help                 Show this message and exit.
```

The `--dry-run` flag is framework-level: it automatically swaps all registered adapters
to their dry-run variants without any project code changes.

## Decision Drivers

- Type-hint-driven philosophy alignment (pydantic, PEP 544 Protocols, now CLI)
- Consistent CLI across all 8+ projects without per-project implementation
- Framework-level `--dry-run` support for adapter swapping
- Minimal API surface — projects should not need to write CLI code

## Considered Options

### Option 1: argparse (stdlib)

Use Python's built-in `argparse` module.

- *Advantages:* No dependency, part of the standard library, well-documented.
- *Disadvantages:* Verbose API for defining arguments. No type-hint-driven parsing.
  Does not align with the type-driven philosophy of the rest of the framework.

### Option 2: Click

Use the Click library for CLI creation.

- *Advantages:* Mature, well-documented, composable commands, widely used.
- *Disadvantages:* Decorator-heavy argument definition does not leverage type hints.
  Typer is built on Click and adds the type-hint layer — Click is the lower-level
  building block.

### Option 3: Python Fire

Use Google's Fire library for automatic CLI generation from functions/classes.

- *Advantages:* Zero configuration — generates CLI from any Python object.
- *Disadvantages:* Too magical — generates CLIs from arbitrary objects, which makes
  the interface unpredictable. Less control over help text and argument validation.
  Smaller community than Click/Typer.

### Option 4: Typer (chosen)

Use Typer for type-hint-driven CLI scaffolding.

- *Advantages:* Type hints drive argument parsing — aligns with pydantic-settings and
  PEP 544 Protocols. Built on Click (inherits its maturity and ecosystem). Modern API
  with excellent auto-completion support. Click is a transitive dependency anyway.
- *Disadvantages:* Additional dependency (though Click comes transitively). Slightly
  more opinionated than raw Click.

## Decision Matrix

| Criterion           | argparse | Click | Python Fire | Typer |
| ------------------- | -------- | ----- | ----------- | ----- |
| Type-hint alignment | 1        | 2     | 3           | 5     |
| Ecosystem maturity  | 5        | 5     | 3           | 4     |
| API simplicity      | 2        | 3     | 5           | 5     |
| Auto-completion     | 1        | 3     | 2           | 5     |
| Dependency weight   | 5        | 3     | 3           | 3     |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- All cosalette applications get a consistent, professional CLI with zero project-specific
  code
- `--dry-run` works across all projects — framework swaps adapters automatically
- Type-hint-driven argument parsing maintains philosophical consistency with pydantic and
  PEP 544 Protocols
- Rich help text and auto-completion support out of the box

### Negative

- Typer is an additional direct dependency (Click is transitive)
- Projects that need custom CLI commands must learn Typer's API for extension

## Amendment (2026-10-05) — Additive

**Rationale:** Typer pulls rich, pygments and markdown-it-py (about 15 MB) into every app image, but they load only for --help and CLI errors. Adopters on 512 MB hosts want to drop them (cos-8jxg.6). Typer has no rich-free distribution: since 0.24 typer-slim is a shim that depends on typer, and typer requires rich. Typer only checks the TYPER_USE_RICH variable, so an image without rich (or without pygments, which rich.syntax imports) crashed on --help and on every usage error.

### Additional Sub-Decision: Plain help when rich is not installed

Every Typer instance cosalette builds (the app CLI, the `schema` group and the `cosalette` package CLI) gets `rich_markup_mode=None` and `pretty_exceptions_enable=False` when rich, pygments or markdown-it-py cannot be found (`_utils._typer_options()`). Help and usage errors then use Click's plain formatter, with the same exit codes. With all three installed nothing changes. cosalette keeps depending on `typer`, and therefore on rich; apps that want a smaller image exclude rich with a uv `override-dependencies` entry, documented in the containerize guide.

### Additional Considered Options

**Make Typer an optional extra**

Move typer to a `cosalette[cli]` extra so the default install has no rich.

- *Advantages:* The default install drops rich, pygments and markdown-it-py without any downstream configuration.
- *Disadvantages:* `--help`, `--version`, the schema and health subcommands and the `cosalette` package CLI would all need a second implementation or would fail on a default install.; A breaking change for every app for an image-size-only gain.

### Additional Positive Consequences

- Images can delete rich, pygments and markdown-it-py without breaking --help or CLI error messages.

### Additional Negative Consequences

- Help output looks different (plain, no panels) in images without rich.
- The default install still contains rich; removing it needs a downstream uv override.
