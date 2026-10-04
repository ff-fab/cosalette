---
status: Accepted
date: 2026-10-04
impact: high
tags: [health, packaging, cli, release]
---

# ADR-087: Native cosalette-health probe binary shipped in platform wheels

## Status

Accepted **Date:** 2026-10-04

## Context

ADR-083 added an opt-in health file and a `health` CLI probe for container liveness. Every probe run starts a Python interpreter and imports the app or the package CLI. On a Raspberry Pi 4 limited to `cpus: 0.5`, an early adopter measured 11-13 s per probe and about 100 times the app's idle CPU. On a 6-core x86_64 host (Python 3.14.7, median of 50 runs) the costs per probe are:

| Probe | Wall | CPU | Max RSS |
|---|---|---|---|
| native `cosalette-health` binary | 1.2 ms | <1 ms | 2.2 MiB |
| stdlib Python fallback (`cosalette._health._probe`) | 60.3 ms | 59.2 ms | 15.1 MiB |
| `cosalette health` (Typer CLI) | 392.8 ms | 392.6 ms | 45.9 MiB |

The health-file check itself is small: read one JSON file, compare `written_at` with the clock, and inspect per-device statuses. cosalette already ships platform wheels built by maturin with a pyo3 extension (ADR-070, cosalette-filters-rs), so a Rust toolchain and a wheel matrix exist. The difficulty is packaging: a wheel can carry an executable in `.data/scripts`, but pip and uv let a `[project.scripts]` entry of the same name overwrite it, `maturin sdist` stores data files as mode 644, and the sdist must keep working on platforms without a prebuilt wheel. This ADR decides how the native probe is built, packaged and named, what happens on platforms without it, and how the health-file contract both implementations read is versioned.

## Decision

Ship a small Rust binary, `cosalette-health`, inside each platform wheel and keep a stdlib-only Python console script of the same name as the fallback for the sdist and local builds, because it cuts the per-probe cost by about 50 times (CPU) and 20 times (RSS) against the stdlib fallback without changing how users invoke it.

**Build and packaging.** The binary lives in the workspace crate `crates/cosalette-health` (only `serde_json`), built with the Cargo profile `probe` (`opt-level = "s"`, LTO, one codegen unit, `panic = "abort"`, stripped; about 370 KB on glibc, 450 KB static musl). The committed `pyproject.toml` declares the console script `cosalette-health = "cosalette._health._probe:main"`. For platform wheels only, `scripts/bundle-health-probe.sh` builds the binary, stages it at `packages/data/scripts/cosalette-health[.exe]`, removes the console-script line and sets `data = "packages/data"` under `[tool.maturin]`. Each wheel therefore contains exactly one `cosalette-health`: the native binary in platform wheels, the Python launcher everywhere else. On Linux the script runs as maturin-action's `before-script-linux` inside the manylinux/musllinux container, which gives the glibc 2.17 floor; on macOS and Windows a separate runner step runs it. `task build:wheel:probe` does the same locally and restores `pyproject.toml` afterwards.

**Fallback.** `cosalette._health._probe` imports only the standard library and implements the same CLI and the same checks. `cosalette health` and `<app> health` call into it, so all three Python entry points share one implementation. The sdist contains neither `packages/data` nor the crate, so installing it from source always yields the Python launcher.

**CLI contract.** `cosalette-health [--file PATH] [--max-age SECONDS] [--fail-on STATUS ...]`, with `--file` overriding `COSALETTE_HEALTH_FILE`. Exit 0 means healthy and 1 means unhealthy; usage errors print `unhealthy: usage error: ...` and exit 1, and a panic prints `unhealthy: internal error` and exits 1, so container runtimes never see another code.

**Health-file contract.** `written_at` is required and finite; `interval` defaults to 60; `devices` maps names to objects with a string `status`; unknown keys are ignored. `render()` now writes `health_file_version: 1`. A missing version means 1; an unknown major version is reported unhealthy. Golden fixtures in `packages/tests/fixtures/health_file_cases.json` are run by both pytest and `cargo test`, so the two implementations cannot drift.

**Platform matrix.** Native binary: manylinux x86_64, aarch64 and armv7; musllinux x86_64 and aarch64; macOS x86_64 and arm64; Windows x86_64. CI checks each wheel with `scripts/check-probe-wheel.py` (one data script, no clashing entry point, executable bit, glibc <= 2.17 on gnu targets) and smoke-tests it in a `python:3.14-slim`/`-alpine` container or natively. Every other platform (for example musllinux armv7) installs the sdist and gets the Python fallback.

**Relation to other ADRs.** Extends ADR-083 (same probe semantics, now with a versioned contract and a cheap implementation). Reuses the ADR-070 maturin build and wheel matrix. Keeps ADR-022's lazy-import goal: the fallback imports nothing from cosalette's heavy dependencies. Under ADR-017, syft already reports no components for a `.whl`; the binary's crates are likewise not visible until it is built with cargo-auditable, which is tracked as a follow-up.

```dockerfile
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD ["cosalette-health"]
```

## Decision Drivers

- Per-probe CPU and memory cost on constrained hosts such as a Raspberry Pi with a CPU quota
- One command name that works on every install, with or without a native wheel
- Exit codes limited to 0 and 1 so orchestrators interpret every outcome correctly
- No drift between the Python and the native checks
- Reuse of the existing maturin build, wheel matrix and Rust toolchain (ADR-070)
- sdist installs must keep working without a Rust toolchain

## Considered Options

### Option 1: Native binary in platform wheels, Python console script elsewhere (chosen)

Build the Rust binary per target and ship it as a wheel data script; a pre-build step removes the same-named console script from platform wheels only. The sdist and local builds keep the stdlib console script.

- *Advantages:* Exactly one `cosalette-health` per wheel, so installer overwrite order does not matter; Same command on every platform; the fallback is automatic; About 1 ms and 2 MiB per probe where the wheel exists
- *Disadvantages:* Platform wheel builds rewrite `pyproject.toml` before maturin runs; A second Rust crate to maintain and audit

### Option 2: Same-name binary and console script in every wheel

Ship both the data script and the `[project.scripts]` entry and rely on the installer to keep the binary.

- *Advantages:* No build-time rewriting of `pyproject.toml`
- *Disadvantages:* pip and uv write the console script after the data scripts, so the Python launcher silently replaces the binary; Behaviour depends on installer internals

### Option 3: Python data-script stub that execs the binary

Commit a launcher stub under the data directory that runs the native binary when present and the Python module otherwise.

- *Advantages:* Single packaging path for wheels and sdist
- *Disadvantages:* `maturin sdist` stores data files as mode 644, so the stub is not executable after a source install; Every probe still starts an interpreter, losing most of the saving

### Option 4: Differently named fallback command

Ship the binary as `cosalette-health` and the Python fallback under another name such as `cosalette-health-py`.

- *Advantages:* No name collision and no build-time rewriting
- *Disadvantages:* Dockerfiles must know which variant is installed; On sdist installs the documented command does not exist

### Option 5: Stdlib Python probe only

Keep only the stdlib-only Python module as the probe and do not build a native binary.

- *Advantages:* No new crate, no wheel changes
- *Disadvantages:* Still about 60 ms CPU and 15 MiB per probe, roughly 50 times the native cost

## Decision Matrix

| Criterion | Native binary in platform wheels, Python console script elsewhere | Same-name binary and console script in every wheel | Python data-script stub that execs the binary | Differently named fallback command | Stdlib Python probe only |
| --- | --- | --- | --- | --- | --- |
| Per-probe cost | 5 | 2 | 2 | 5 | 2 |
| Same command on every install | 5 | 4 | 2 | 1 | 5 |
| Deterministic install result | 5 | 1 | 3 | 5 | 5 |
| sdist works without Rust | 5 | 5 | 2 | 3 | 5 |
| Build and maintenance complexity | 3 | 4 | 3 | 4 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- `HEALTHCHECK CMD ["cosalette-health"]` costs about 1 ms of CPU and 2 MiB per probe on supported platforms
- Platforms without a native wheel get the same command through the stdlib fallback, still about 6 times cheaper than `cosalette health`
- The health-file contract is versioned and enforced by shared golden fixtures in both implementations
- Exit codes are limited to 0 and 1, including usage errors and panics

### Negative

- Platform wheel builds depend on a script that edits `pyproject.toml`; local builds must use `task build:wheel:probe` to get the binary
- A second Rust crate needs MSRV, clippy, cargo-deny and audit upkeep
- The binary's dependencies do not appear in syft SBOMs until it is built with cargo-auditable
- Changes to the health-file check must be made in Python and Rust together

_2026-10-04_
