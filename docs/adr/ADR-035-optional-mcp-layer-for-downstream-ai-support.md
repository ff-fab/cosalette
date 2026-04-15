---
status: Accepted
date: 2026-04-14
impact: high
tags: [packaging, cli, documentation, architecture, dependencies]
---

# ADR-035: Optional MCP Layer for Downstream AI Support

## Status

Accepted **Date:** 2026-04-14

## Context

cosalette provides downstream AI support through a two-layer model established in ADR-034: a compact static instruction file (<200 lines) installed via `cosalette ai init`, and runtime-discoverable help topics via `cosalette ai help <topic>`. This model works well for onboarding but has limitations: agents must shell out and parse text output for help queries, cannot programmatically query app-specific registrations or configuration schemas, and lack access to framework architectural decisions.

The MCP (Model Context Protocol) ecosystem has matured — VS Code Copilot, Cursor, Windsurf, and Claude Code all support MCP servers natively, and the Python `fastmcp` SDK provides a stable async-native foundation. The question is what features to expose via MCP and how to package them without breaking the existing CLI baseline.

A planning evaluation identified six feature candidates and three packaging scenarios:

- **F1 — Framework Guidance Tools:** Structured help topics, bootstrap overview, conventions summary
- **F2 — App Introspection:** Registry snapshot, device details, adapter mappings via `build_registry_snapshot()`
- **F3 — Configuration Schema:** JSON Schema for Settings classes, environment variable listing
- **F4 — Code Scaffolding:** App-aware generation of device handlers, adapter Protocol pairs, and tests
- **F5 — ADR Context:** Queryable index of architectural decisions with full content retrieval
- **F6 — Diagnostics:** Static analysis of app code and error explanation *(deferred)*

After evaluation, features F1–F5 were selected for implementation and F6 was deferred until downstream usage validates the need.

## Decision

Add an optional MCP server via `cosalette[mcp]` optional extra, exposing five feature groups — framework guidance tools (F1), app introspection (F2), configuration schema (F3), code scaffolding (F4), and ADR context (F5) — because this gives downstream agents structured, app-aware access to framework knowledge while keeping the CLI and static instruction file as the zero-dependency baseline.

The MCP server complements, not replaces, the existing CLI. The `fastmcp` SDK provides the server implementation. Transport defaults to stdio for IDE integration, with optional SSE for team/remote setups. The server is launched via `cosalette ai mcp serve` and `python -m cosalette.mcp`. App introspection uses static module import with graceful fallback when hardware libraries are missing. ADR metadata is packaged as a compact JSON index in the wheel. Scaffolding tools use Jinja2 templates with smoke tests to catch API drift. The `cosalette ai init` command manages `.vscode/mcp.json` when `cosalette[mcp]` is installed.

```bash
# Install with MCP support
uv add cosalette[mcp]

# Bootstrap downstream repo (installs instructions + .vscode/mcp.json)
cosalette ai init

# Start MCP server (stdio, for IDE integration)
cosalette ai mcp serve

# Start MCP server (SSE, for team/remote)
cosalette ai mcp serve --transport sse --port 8080
```

## Decision Drivers

- Downstream agents need structured access to framework guidance without shelling out and parsing CLI text output
- App-specific introspection (registry snapshots, DI graph, config schema) cannot be delivered effectively through static instruction files
- The MCP ecosystem (VS Code Copilot, Cursor, Windsurf, Claude Code) has matured to the point where MCP server support is ubiquitous in developer tooling
- Code scaffolding tools reinforce cosalette's declarative, self-documenting app.py pattern by steering agents toward canonical framework usage
- MCP must be optional — the CLI and static instruction file remain the zero-dependency baseline for environments without MCP support
- ADR context via structured queries helps agents understand framework design rationale without manual documentation search
- Template maintenance burden can be mitigated through smoke tests (render → compile → lint) and agent instruction rules

## Considered Options

### Option 1: Guidance-only MCP server (F1 + F5)

Expose only the curated help content and ADR index as MCP tools. No app introspection, no config schema, no scaffolding — equivalent to wrapping the CLI help surface in structured tool responses.

- *Advantages:* Very small surface area — approximately 300 lines of new code; Content reuses existing help functions from _package_cli.py (DRY); No need to import or analyze downstream app modules; Lowest maintenance burden
- *Disadvantages:* Does not leverage MCP's strength for structured, contextual data; Agents still need to read app source to understand registrations and configuration; Marginal improvement over CLI for agents that can already shell out

### Option 2: Introspection-focused MCP server (F1 + F2 + F3 + F5)

Add app-aware introspection tools (registry snapshot, device details, adapter mappings) and configuration schema queries to the guidance surface, without code scaffolding. Approximately 750 lines of new code.

- *Advantages:* Agents get structured, contextual data about the specific downstream app being built; Config schema surface catches misconfigurations before runtime; Reuses build_registry_snapshot() from existing _introspect.py module; Moderate maintenance burden — no template management
- *Disadvantages:* No scaffolding tools — agents must write all framework code from instruction file patterns alone; Misses the opportunity to reinforce declarative app patterns through generated code; Still requires agents to understand decorator syntax and DI wiring from documentation

### Option 3: Full framework assistant with scaffolding (F1 + F2 + F3 + F4 + F5) (chosen)

Complete MCP surface with guidance, introspection, config schema, code scaffolding, and ADR context. Scaffolding tools generate correct-by-construction device handlers, adapter Protocol pairs, and test files using Jinja2 templates that are app-aware (avoiding name collisions, suggesting correct DI types). Diagnostics (F6) are explicitly deferred. Approximately 1200–1400 lines of new code.

- *Advantages:* Agents can generate framework-correct code in a single MCP tool call; Scaffolding is app-aware — uses introspection to avoid name collisions and suggest DI types from registered adapters; Reinforces cosalette's declarative, self-documenting app.py/main.py philosophy over complex factory patterns; Template smoke tests (render → compile → ruff check) catch API drift automatically in CI; Complete structured query surface for all non-diagnostic framework knowledge
- *Disadvantages:* Higher implementation effort (approximately 1200–1400 lines across the _mcp package); Template maintenance requires discipline — mitigated by smoke tests and agent instruction rules; Jinja2 becomes an additional dependency in the optional extra; Generated code quality must be validated through smoke tests rather than runtime verification

## Decision Matrix

| Criterion | Guidance-only MCP server (F1 + F5) | Introspection-focused MCP server (F1 + F2 + F3 + F5) | Full framework assistant with scaffolding (F1 + F2 + F3 + F4 + F5) |
| --- | --- | --- | --- |
| Value over CLI baseline | 2 | 4 | 5 |
| App-awareness of agent responses | 1 | 5 | 5 |
| Reinforcement of framework conventions | 2 | 3 | 5 |
| Maintenance burden (inverted: 5=low) | 5 | 4 | 3 |
| Implementation effort (inverted: 5=low) | 5 | 4 | 2 |
| Downstream time-to-first-device | 2 | 3 | 5 |
| Graceful degradation without MCP | 5 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Downstream agents get structured, app-aware access to framework guidance, introspection, configuration schema, and code scaffolding via a standard protocol
- The CLI and static instruction file remain fully functional as the zero-dependency baseline — MCP is purely additive
- Scaffolding tools steer downstream developers toward canonical declarative patterns rather than ad-hoc factory indirections
- ADR context becomes queryable, helping agents understand framework design rationale for better code decisions
- Template smoke tests create an automated safety net for API drift between framework evolution and scaffolding templates
- The cosalette ai init command provides single-command MCP bootstrapping alongside instruction file installation

### Negative

- cosalette must maintain a new optional package surface (_mcp/) with approximately 1200–1400 lines of code across guidance, introspection, config, scaffolding, and ADR modules
- Jinja2 and fastmcp become dependencies of the optional extra, increasing the dependency surface for MCP users
- Template maintenance requires ongoing discipline — framework API changes must be reflected in scaffolding templates, enforced by smoke tests and agent instruction rules
- App introspection via static module import may fail when hardware-specific dependencies are missing in development environments, requiring graceful fallback handling
- A future diagnostics layer (F6) will need separate evaluation, adding another decision point

_2026-04-14_
