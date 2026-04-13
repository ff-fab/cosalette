# COS-0k3 Phase 1 Plan — AI-Friendly Downstream Context

This planning note captures the approved first-phase direction for COS-0k3 and
the formal decision in [ADR-034](../adr/ADR-034-ai-friendly-downstream-framework-context.md).

## Chosen Approach

Use a two-layer downstream AI support model:

- A compact static instruction file for downstream repos, targeting fewer than
  200 lines and limited to conventions, constraints, and failure-prone
  framework patterns.
- A package-level `cosalette` bootstrap/help workflow that installed users can
  run immediately after `pip install cosalette` or `uv add cosalette`.
- A future MCP path as an optional structured-query layer, explicitly deferred
  until downstream tooling support makes it worthwhile.

## Rejected Alternatives

- Static file distribution only: simple, but too manual and too easy to let
  drift from the framework.
- MCP-first: attractive long term, but not reliable enough as the day-one path
  for downstream repos.
- Generation-only help: likely to produce verbose API dumps instead of the
  focused guidance agents actually need.

## Downstream Onboarding UX

The downstream path should be simple and immediate:

1. Install cosalette in the app repo.
2. Run a package-level bootstrap command such as `cosalette ai init`.
3. Let that command install or refresh the compact instruction file.
4. Use runtime help commands such as `cosalette ai help telemetry` when deeper
   framework context is needed.

The baseline UX should avoid telling users to copy files manually from the docs
site or the framework repository.

## Implementation Phases

### Phase 1 — Package-Level Bootstrap Surface

Deliver the public downstream entry point and packaged assets.

Likely files/modules to touch:

- `pyproject.toml`
- `packages/src/cosalette/_cli.py`
- a new package CLI/bootstrap module such as `packages/src/cosalette/_agent_cli.py`
- packaged template/help assets under `packages/src/cosalette/`

### Phase 2 — Compact Static Context + Runtime Help

Replace the oversized downstream reference with a compact contract and move
detail behind runtime-discoverable help topics.

Likely files/modules to touch:

- `docs/reference/cosalette-framework-reference.instructions.md`
- packaged AI instruction templates and help-topic assets
- `docs/getting-started/index.md`
- `docs/getting-started/quickstart.md`

### Phase 3 — Optional MCP Layer

Add a structured-query surface only after the package CLI and compact
instruction workflow are established.

Likely files/modules to touch:

- optional MCP-specific package/module(s)
- packaging metadata for optional extras or companion distribution
- downstream documentation for MCP enablement and fallback behavior

The MCP layer is a future extension, not the default onboarding mechanism.
