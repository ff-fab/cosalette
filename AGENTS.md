# Agent Instructions

## Issue Tracking

This project uses **bd (beads)** for issue tracking. Run `bd prime` for workflow
context, or install hooks (`bd hooks install`) for auto-injection.

**Quick reference:**

- `bd ready` - Find unblocked work
- `bd create "Title" --type task --priority 2` - Create issue
- `bd close <id>` - Complete work
- `bd dolt pull` / `bd dolt push` - Exchange issue data with the remote (also auto-run
  by the post-merge / pre-push git hooks). The `.beads/issues.jsonl` export is
  local-only and gitignored (F-SC1) — never commit it.

For full workflow details: `bd prime`

## Tooling Policy

**Use `task <name>`** for all operations (run `task --list`). Fall back to `uv run` only
when no task exists. Never invoke `python` directly.

For `gh` subcommands without a task wrapper, direct invocation is fine.

### Gate Tasks

Deferred work, technical debt and TODOs to revisit later get a **gate task** in beads as
a dependency of the relevant work item.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->

<!-- END BEADS INTEGRATION -->
