---
status: Accepted
date: 2026-05-10
impact: moderate
tags: [architecture, di]
---

# ADR-047: Source module hierarchy: move remaining root-level modules into subpackages

## Status

Accepted **Date:** 2026-05-10

## Context

The cosalette package (`packages/src/cosalette/`) has adopted a subpackage-per-concern layout as its dominant organisational pattern. Seventeen subpackages — `_app/`, `_context/`, `_runners/`, `_wiring/`, `_registration/`, `_persistence/`, `_ai_content/`, `_settings/`, `_mqtt/`, `_cron/`, `_health/`, `_strategies/`, `_package_cli/`, `_schema/`, `_mcp/`, `_router/`, and `testing/` — are each first-class namespaces with their own `__init__.py`.

Eight modules remain as flat `.py` files at the package root, inconsistent with this established pattern:

| Module | Lines | Importers | Proposed destination |
|---|---|---|---|
| `_stream.py` | 394 | 17 | `_runners/` (stream primitives consumed by `_stream_runner.py`) |
| `_adapter_lifecycle.py` | 830 | 10 | `_wiring/` (lifecycle is a wiring-phase concern) |
| `_periodic.py` | 121 | 9 | `_runners/` (periodic task primitive) |
| `_introspect.py` | 678 | 7 | `_mcp/` (used exclusively by MCP layer and its tests) |
| `_contracts.py` | 611 | 6 | `_runners/` (contract validation shared by both runners) |
| `_reactors.py` | 400 | 4 | `_wiring/` (reactor framework used during bootstrap) |
| `_command_runner.py` | 682 | 3 | new `_commands/` subpackage alongside `_command.py` |
| `_filters.py` | 61 | 2 | `_strategies/` (signal filter primitives) |

Combined: 3,777 lines, 58 import references across the codebase. `_stream.py` has the highest blast radius with 17 importers — a hard cut-over would require touching 17 call sites in a single PR. The chosen migration strategy uses stub re-export files at the old paths (Strangler Fig pattern) so that `from cosalette._stream import ...` style imports keep working throughout the transition. Stubs will be removed in a dedicated follow-up PR once all call sites are updated.

## Decision

Move all 8 remaining root-level modules into their designated subpackages using `git mv`, update intra-package imports to reflect the new locations, and place a stub re-export module at each old path for the duration of the transition. The migration strategy is two-phase: (1) move + add stubs — zero breakage for any downstream import; (2) remove stubs once all call sites reference the canonical subpackage path. Stub re-exports are preferred over a direct cut-over because `_stream.py`'s 17 importers make a single-PR hard cut-over impractically large and risky.

## Decision Drivers

- Consistency: 17 of 25 cosalette concerns already live in subpackages; the 8 flat files are visual noise in the package root and contradict the established pattern.
- Navigability and on-boarding: grouping stream primitives, lifecycle, and MCP introspection into their natural subpackages makes the root `__init__.py` immediately readable and reduces the time needed to locate relevant code.
- Single Responsibility Principle: each subpackage can evolve its own `__init__.py` re-exports, internal helpers, and tests without polluting the global namespace or forcing consumers to know internal layout.
- Blast-radius management: `_stream.py` has 17 importers — the highest of any module being moved. A stub re-export at `cosalette/_stream.py` decouples the structural move from the call-site migration, allowing each to land in a separate PR.

## Considered Options

### Option 1: Keep flat root files

Leave all 8 modules as top-level `.py` files under `packages/src/cosalette/`. No structural changes are made; existing imports continue to work without modification.

- *Advantages:* Zero migration effort and zero risk of import breakage.; No temporary stub indirection.
- *Disadvantages:* Grows technical debt: the inconsistency between flat modules and subpackages becomes harder to resolve as the codebase grows.; The package root remains cluttered with 8 files that belong logically inside existing subpackages.; Inconsistent with 68 % of the codebase already using the subpackage pattern, making navigation harder for new contributors.

### Option 2: Move into subpackages with stub re-exports (chosen)

Use `git mv` to relocate each module to its designated subpackage, update intra-package imports, and place a one-line stub re-export at the old path (e.g. `cosalette/_stream.py` re-exports everything from `cosalette._runners._stream`). Stubs are removed in a follow-up PR after all call sites are migrated. This is the Strangler Fig pattern applied to import paths.

- *Advantages:* Achieves full structural consistency with the subpackage convention.; Stub re-exports make the move backward-compatible: no external or internal import breaks on day one.; Two-phase approach limits per-PR risk: the structural PR is reviewable in isolation; the clean-up PR removes stubs once CI confirms no remaining old-path imports.; Each module's new home is semantically obvious, reducing cognitive load.
- *Disadvantages:* Stub files create temporary indirection for the duration of the transition period.; Requires two PRs rather than one.

## Decision Matrix

| Criterion | Keep flat root files | Move into subpackages with stub re-exports |
| --- | --- | --- |
| Consistency with subpackage convention | 1 | 5 |
| Import breakage risk | 5 | 4 |
| Navigability and discoverability | 2 | 5 |
| Migration safety (blast-radius control) | 5 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- The package root becomes a clean index of concerns: all 25 concerns live in named subpackages, with zero flat orphan files.
- Each module's location is semantically motivated — stream primitives in `_runners/`, lifecycle in `_wiring/`, introspection in `_mcp/` — making the codebase more navigable for contributors.
- Stub re-exports provide full backward compatibility during transition: `from cosalette._stream import X` continues to work unchanged.
- The two-phase approach allows structural and clean-up changes to be reviewed and merged independently, reducing per-PR cognitive load.

### Negative

- Stub files introduce a temporary layer of indirection that persists until the follow-up clean-up PR lands.
- Two PRs are required instead of one; the clean-up PR must not be skipped or the stubs will become permanent.
- Reviewers must understand the Strangler Fig intent to evaluate the stub files correctly — inline comments in each stub are essential.

_2026-05-10_
