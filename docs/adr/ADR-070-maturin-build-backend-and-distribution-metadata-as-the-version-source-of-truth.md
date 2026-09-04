---
status: Accepted
date: 2026-09-04
impact: moderate
tags: [packaging, release, dependencies]
---

# ADR-070: maturin Build Backend and Distribution Metadata as the Version Source of Truth

## Status

Accepted **Date:** 2026-09-04 | Supersedes ADR-008

## Context

ADR-008 recorded hatchling as the build backend and setuptools-scm as the version manager, writing a generated `src/cosalette/_version.py` from git tags. Both halves of that record have since stopped matching the repository.

**The build backend changed and was never re-recorded.** When the Rust signal-filter extension landed (ADR-022), the project moved to maturin so that `cosalette-filters-rs` could be compiled into the wheel as `cosalette._filters_rs`. `pyproject.toml` has read `requires = ["maturin>=1.12,<2"]` / `build-backend = "maturin"` ever since, with a `[tool.maturin]` block declaring `python-source = "packages/src"`, the abi3-py314 pyo3 features, and the crate manifest path. No ADR covered that migration — ADR-022 mentions maturin only in passing, as a note that development requires a Rust toolchain for `maturin develop`. setuptools-scm was consequently never part of the build at all: maturin reads the static `[project] version` field and ignores `[tool.setuptools_scm]` entirely.

**The version mechanism drifted into two disagreeing APIs.** The `[tool.setuptools_scm]` block survived as dead configuration, and `_version.py` was kept alive only by a devcontainer `post-create.sh` step that regenerated it via `scripts/update_version.py`. `cosalette.__version__` preferred that generated, gitignored module and fell back to `importlib.metadata` only when it was missing. In a source checkout the two runtime version APIs therefore returned different strings: `cosalette.__version__` reported `0.7.2.dev8+g3c640a46e` (derived by setuptools-scm from the last tag plus commit distance) while `importlib.metadata.version("cosalette")` reported `0.8.0` (the installed distribution's metadata). Anything reporting the framework version — the health heartbeat's `version` field, `--version` output, log records — could disagree with what a dependency resolver or an SBOM saw for the same process.

Release-time versioning is meanwhile already consolidated and consistent: release-please bumps `[project] version` in `pyproject.toml` (marked `# x-release-please-version`), records the same value in `.release-please-manifest.json` (`{".": "0.8.0"}`), and tags the commit (`v0.8.0`). All three agree. Only the runtime read path was ambiguous.

## Decision

Use the installed distribution's metadata as the single runtime source of `cosalette.__version__`, and record **maturin** as the build backend, superseding ADR-008's hatchling + setuptools-scm design.

`cosalette/__init__.py` reads `importlib.metadata.version("cosalette")` directly, with a single fallback for the not-installed case. No generated `_version.py` module exists anywhere in the package any more, `[tool.setuptools_scm]` and the `setuptools-scm[toml]` dev dependency are removed, `scripts/update_version.py` is deleted, the devcontainer step that regenerated the module is gone, and the `**/_version.py` gitignore entry is dropped.

The release-time inputs are unchanged and remain the authority for *what* that metadata says: release-please writes `[project] version` in `pyproject.toml`, mirrors it in `.release-please-manifest.json`, and tags the commit; maturin bakes that static version into the wheel's `METADATA`, which is exactly what `importlib.metadata` reads back at runtime. Everything else about ADR-008 that is still true — PyPI as the distribution channel, the package name `cosalette`, src layout, the PEP 561 `py.typed` marker, independent semver with `cosalette>=0.5,<1.0`-style consumer pins — carries forward unchanged.

```python
# packages/src/cosalette/__init__.py
from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: installed distribution metadata, so that
    # cosalette.__version__ and importlib.metadata.version("cosalette")
    # can never disagree.
    __version__: str = version("cosalette")
except PackageNotFoundError:
    # Not installed at all (e.g. running straight from a source tree).
    __version__ = "0.0.0+unknown"


# pyproject.toml — the release-time inputs
# [project]
# version = "0.8.0"  # x-release-please-version
#
# [build-system]
# requires = ["maturin>=1.12,<2"]
# build-backend = "maturin"
```

## Decision Drivers

- The two runtime version APIs must never disagree — `cosalette.__version__` and `importlib.metadata.version("cosalette")` reported `0.7.2.dev8+g3c640a46e` and `0.8.0` for the same process
- The recorded build backend must match `pyproject.toml`: maturin has been the backend since the Rust filter extension landed (ADR-022), and maturin reads the static `[project] version`, so setuptools-scm was dead configuration that never participated in a build
- The version a running app reports (health heartbeat `version` field, log records, `--version`) must equal the version a dependency resolver, an SBOM, or a downstream pin sees for that same installation
- One mechanism, not two: a generated, gitignored module kept alive by a devcontainer post-create step is invisible in the source tree and silently absent for anyone who did not run that step
- Release-time versioning is already consolidated on release-please (`[project] version`, `.release-please-manifest.json`, git tag) and needs no second, parallel version pipeline
- Standard-library-only read path — `importlib.metadata` is stdlib, so the runtime version lookup adds no dependency and no build-time code generation

## Considered Options

### Option 1: hatchling + setuptools-scm generated _version.py

The ADR-008 design: hatchling as the build backend with setuptools-scm deriving the version from git tags and writing a generated `src/cosalette/_version.py`, which `__init__.py` imports in preference to distribution metadata.

- *Advantages:* Git tags drive version numbers with no manual bump step; A source checkout reports a meaningful development version (`0.7.2.dev8+g3c640a46e`) that encodes distance from the last tag
- *Disadvantages:* Incompatible with the actual build backend — maturin is required for the pyo3 extension and reads the static `[project] version`, so setuptools-scm never ran during a build; Produces two disagreeing runtime version APIs, the concrete defect this ADR exists to remove; The generated module is gitignored and therefore absent unless a devcontainer post-create step regenerated it, making behaviour depend on developer environment setup; Duplicates release-please, which already owns version bumping across `pyproject.toml`, the manifest, and the tag

### Option 2: maturin + static [project] version, __version__ from importlib.metadata (chosen)

Keep maturin as the build backend with a static `[project] version` maintained by release-please, and have `cosalette.__version__` read the installed distribution's metadata through `importlib.metadata`, with a `0.0.0+unknown` fallback when the package is not installed.

- *Advantages:* The two runtime version APIs are the same call, so they cannot drift apart by construction; Matches the build backend actually in use — maturin bakes `[project] version` into the wheel `METADATA` that `importlib.metadata` reads back; No generated files, no build-time code generation, no gitignored module, no devcontainer bootstrap step; Stdlib-only read path; drops the `setuptools-scm[toml]` dev dependency and `scripts/update_version.py` entirely; The version a process reports is by definition the version of the distribution that was installed, which is what SBOMs, resolvers, and downstream pins see
- *Disadvantages:* An uninstalled source tree reports `0.0.0+unknown` instead of a tag-derived development version; Requires an editable or real install before the runtime version is meaningful; Version bumps depend on release-please keeping `[project] version`, `.release-please-manifest.json`, and the git tag in step

### Option 3: maturin with a dynamic git-tag-derived version

Keep maturin but declare `version` dynamic and derive it from git tags at build time via a maturin-compatible SCM plugin, so wheel metadata and the runtime version both come from the tag.

- *Advantages:* Retains tag-driven versioning with no manual bump step; Wheel metadata and runtime version stay a single value, so the disagreement defect would also be fixed
- *Disadvantages:* Adds a build-time dependency and a maturin plugin integration for a problem release-please already solves; Fights release-please, which is configured to write the static `[project] version` and the manifest — either the tag or release-please must become subordinate; Builds from a source archive or a shallow CI clone with no tags silently produce a wrong version; Larger change surface than the defect warrants, for no gain over reading the metadata that is already correct

## Decision Matrix

| Criterion | hatchling + setuptools-scm generated _version.py | maturin + static [project] version, __version__ from importlib.metadata | maturin with a dynamic git-tag-derived version |
| --- | --- | --- | --- |
| Runtime API consistency | 1 | 5 | 4 |
| Matches the actual build backend | 1 | 5 | 4 |
| Mechanism count / maintenance burden | 2 | 5 | 3 |
| Fits the existing release-please pipeline | 2 | 5 | 2 |
| Source-tree developer experience | 4 | 3 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- `cosalette.__version__` and `importlib.metadata.version("cosalette")` are the same call and can never disagree; a regression test asserts the equality
- The recorded build backend finally matches `pyproject.toml` — maturin, with `[tool.maturin]` compiling `cosalette-filters-rs` into `cosalette._filters_rs` (ADR-022)
- No generated `_version.py` exists in the package; nothing is gitignored, nothing needs regenerating, and the module inventory in the source tree is complete as checked in
- `scripts/update_version.py`, the `.devcontainer/post-create.sh` regeneration step, the `[tool.setuptools_scm]` block, the `setuptools-scm[toml]` dev dependency, and the `**/_version.py` gitignore entry are all removed
- The version reported by a running app is exactly the version of the installed distribution, so heartbeat payloads, logs, SBOMs, and dependency resolvers agree
- Release-time versioning has one owner: release-please writes `[project] version` and `.release-please-manifest.json` and tags the commit

### Negative

- An uninstalled source tree reports `0.0.0+unknown` — running straight from `packages/src` without an editable install no longer yields a meaningful version string
- Development versions no longer encode distance from the last tag; every build between releases reports the released version already written in `[project] version`
- Correctness of the reported version now depends on release-please keeping `pyproject.toml`, `.release-please-manifest.json`, and the git tag in agreement — a manual edit to one of the three would go unnoticed at runtime
- The version is only observable after an install step, so tooling that imports the package from a bare checkout must tolerate the `0.0.0+unknown` sentinel

_2026-09-04_
