---
description: 'Documentation - Markdown and Zensical conventions'
applyTo: '**/*.md'
---

# Documentation Instructions

## Documentation System

| Component      | Choice                                                         |
| -------------- | -------------------------------------------------------------- |
| Site Generator | Zensical (`zensical.toml`)                                     |
| Theme          | Zensical (modern theme)                                        |
| Structure      | Structured "information typing" (DITA): Concept/Task/Reference |
| CLI            | `task docs:serve`, `task docs:build`                           |

## ADR Format

**Do not write ADR Markdown directly.** Use the `adr-create` skill, which
produces a schema-conforming JSON document. The renderer (`scripts/render_adr.py`)
performs structural validation and renders canonical Markdown via
`task adr:create`.

| Resource | Path |
|----------|------|
| JSON Schema | `.github/agents/schemas/adr-input.schema.json` |
| Renderer | `scripts/render_adr.py` |
| Task | `task adr:create -- <input.json>` |
| Skill | `.github/skills/adr-create/SKILL.md` |

All ADRs include YAML frontmatter with `status`, `date`, `impact`, and `tags`.

### ADR Operations

| Operation | JSON `type` | Description |
|-----------|-------------|-------------|
| New ADR | `"new"` | Creates `docs/adr/ADR-NNN-slug.md` (auto-numbered) |
| Amend ADR | `"amendment"` | Appends amendment section to existing ADR |
| Supersede ADR | `"supersede"` | Creates new ADR, marks old as superseded |

### Impact Levels & Decision Matrix Requirements

| Impact | Decision matrix | Min options | When to use |
|--------|-----------------|-------------|-------------|
| `low` | Optional | 2 | Single-module convention, naming, tooling |
| `moderate` | **Required** (≥3 criteria) | 2 | Multiple modules, new dependency |
| `high` | **Required** (≥5 criteria) | 3 | Architectural pattern, cross-cutting, breaking |

## File Locations

| Content       | Location    |
| ------------- | ----------- |
| Documentation | `docs/`     |
| ADRs          | `docs/adr/` |

(ADRs are to be included in the main documentation site)
