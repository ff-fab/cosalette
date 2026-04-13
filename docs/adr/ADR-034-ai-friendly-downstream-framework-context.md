---
status: Accepted
date: 2026-04-12
impact: high
tags: [documentation, cli, packaging, architecture]
---

# ADR-034: AI-Friendly Downstream Framework Context

## Status

Accepted **Date:** 2026-04-12

## Context

cosalette is consumed primarily from downstream application repositories after installation via `pip install cosalette` or `uv add cosalette`, not from the framework repo itself.

The current downstream AI reference, `docs/reference/cosalette-framework-reference.instructions.md`, is 824 lines long. That is too large to serve as an injected instruction file: it mixes stable conventions with broad API reference material, costs tokens on every interaction, and makes it harder for agents to focus on the failure-prone parts of the framework.

Today cosalette provides application CLI scaffolding through ADR-005, but it does not expose a package-level `cosalette` bootstrap command that installed downstream users can run before they even have an app-specific CLI. That leaves onboarding to manual copying, browsing hosted docs, or pasting ad-hoc snippets into downstream repos.

The decision therefore needs to define a day-one downstream experience for AI-assisted development: a compact static instruction contract focused on conventions and pitfalls, plus a runtime-discoverable help surface for deeper framework context. MCP remains attractive for structured queries, but support across downstream IDEs and agent clients is still uneven enough that it cannot be the primary mechanism in the first delivery phase.

## Decision

Use a two-layer downstream AI support model: a compact static instruction file shipped and installed through a package-level `cosalette` bootstrap workflow, plus runtime-discoverable help topics exposed by the same installed package, because this gives downstream repos an immediate post-install onboarding path while keeping static agent context small.

Day one will standardize on `cosalette ai` as the canonical public namespace for AI-agent-related commands. The explicit namespace keeps this surface clear in CLI help, leaves room for future AI-oriented commands, and avoids making the top level ambiguous as the package CLI grows. For high-frequency downstream workflows, `cosalette init` and `cosalette prime` are supported shorthand aliases for the corresponding canonical commands, preserving beads-style ergonomics and reducing typing without changing the primary public shape. Topic help remains namespaced under `cosalette ai`; top-level `help` is not canonical. The static file should target fewer than 200 lines and limit itself to conventions, constraints, and failure-prone framework patterns. Detailed API and framework context moves behind runtime-discoverable commands such as topic help or printed reference snapshots.

The wheel and sdist must carry the bootstrap templates and help assets so the workflow works for installed users without a framework repo checkout and without assuming hosted docs access. A future MCP layer may be added later as an optional structured-query surface, but it must complement the package CLI rather than replace the day-one baseline.

```bash
uv add cosalette
cosalette ai init
cosalette init
cosalette ai prime
cosalette prime
cosalette ai help telemetry
cosalette ai help testing
```

## Decision Drivers

- Installed downstream users need a single post-install onboarding command rather than manual file copying from framework docs
- Static AI guidance must stay compact (<200 lines target) and focus on conventions, constraints, and failure-prone patterns
- Detailed framework context must be discoverable at runtime from an installed package, not only from the framework repository or hosted site
- The first delivery phase must work with current agent and IDE capabilities, not only future MCP-capable clients
- Packaging must support offline or low-connectivity development from wheel and sdist artifacts
- Public command naming should balance an explicit namespace for clarity and future growth against shorthand aliases for high-frequency downstream workflows
- The design should leave room for a future structured MCP layer without forcing extra dependencies or adoption friction on day one

## Considered Options

### Option 1: Package-level bootstrap CLI + compact instructions (chosen)

Expose a top-level `cosalette` CLI for installed users. Ship a compact downstream instruction template and packaged help topics inside the wheel and sdist. Downstream repos run a bootstrap command to install or refresh the static instruction file and use CLI help commands for deeper context.

- *Advantages:* Works immediately after `pip install cosalette` or `uv add cosalette`; Keeps the static instruction contract small while still making deep context available on demand; Supports offline and local development because assets are packaged with the library; Maps cleanly onto the existing Typer-based CLI approach from ADR-005
- *Disadvantages:* Requires a new public package-level CLI surface and packaged data maintenance; Introduces another documentation surface that must stay aligned with hosted docs

### Option 2: Static file distribution only

Publish a single compact instruction file or template and tell downstream users to copy it into their repo manually or from hosted documentation. Keep all deeper context in normal docs pages only.

- *Advantages:* Simplest initial implementation; No new CLI entry point or runtime help surface required
- *Disadvantages:* Manual copying is easy to skip, misapply, or let drift; Does not give agents a runtime-discoverable path to deeper framework context; Relies on hosted docs or repository browsing for details

### Option 3: MCP-first structured query surface

Make MCP the primary downstream interface from the start, using structured tool calls for framework help and topic lookups rather than a package CLI and packaged templates.

- *Advantages:* Best long-term fit for structured, selective context retrieval; Avoids teaching agents a command-line help protocol once MCP is available
- *Disadvantages:* Downstream IDE and agent support is still uneven, so day-one onboarding would exclude some users; Raises the implementation and packaging bar before the basic downstream workflow exists; Does not provide a low-friction fallback for environments without MCP

### Option 4: Generated help from code and docs

Generate downstream instructions and help output directly from docstrings, exports, or existing documentation pages at build time or on demand, with minimal hand-curated assets.

- *Advantages:* Reduces some manual duplication between docs and downstream help; Can scale as the framework surface grows
- *Disadvantages:* Generated output tends to over-index on API enumeration rather than failure-prone conventions; High risk of producing verbose or noisy guidance that breaks the compact static target; Still needs an onboarding surface for installed downstream users

## Decision Matrix

| Criterion | Package-level bootstrap CLI + compact instructions | Static file distribution only | MCP-first structured query surface | Generated help from code and docs |
| --- | --- | --- | --- | --- |
| Immediate downstream onboarding after install | 5 | 2 | 2 | 3 |
| Static-context efficiency for AI agents | 5 | 3 | 5 | 2 |
| Works from installed wheel or sdist without repo checkout | 5 | 2 | 2 | 4 |
| Compatibility with current downstream tooling | 5 | 4 | 1 | 3 |
| Future extensibility toward structured help and MCP | 4 | 2 | 5 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Downstream app authors get a single, concrete onboarding path immediately after installing cosalette
- The static downstream instruction file can shrink from an 824-line reference toward a compact contract focused on conventions and common mistakes
- Detailed framework context becomes runtime-discoverable without requiring a repo checkout or permanent token cost
- `cosalette ai` provides a clear public namespace that can grow without crowding the CLI top level, while `cosalette init` and `cosalette prime` keep frequent workflows terse
- The package CLI becomes a stable bridge that future MCP support can augment rather than replace
- Packaged assets and templates make the workflow available in local, CI, and offline environments

### Negative

- cosalette must introduce and maintain a new public package-level CLI entry point and asset packaging story
- Documentation and help output must keep the canonical namespace and shorthand aliases aligned so users understand which forms are primary
- Framework help content will need deliberate curation to avoid duplicating the hosted docs verbatim
- A later MCP layer will add another interface that must stay conceptually aligned with the CLI help surface

_2026-04-12_
