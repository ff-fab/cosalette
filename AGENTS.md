# Agent Instructions

<!-- bd-doctor-divergence: ok -->
<!-- The divergence between AGENTS.md and CLAUDE.md is deliberate: CLAUDE.md is an
     index into .github/instructions/*, this file is a standalone quick reference for
     agents that read only AGENTS.md. Do not symlink them together. -->

## Hard Rules

- **Never merge a pull request** unless the user explicitly asks ("merge this", "land
  it"). Your job ends at creating the PR and waiting for CI — even if everything is
  green. Do not enable auto-merge.
- **Never push directly to `main`.** Branch, PR, squash-merge.
- **Never hand-write ADR Markdown.** ADRs live in `docs/adr/`; create them with the
  `adr-create` skill (`task adr:create`), which renders canonical Markdown from
  schema-validated JSON.

## Issue Tracking

This project uses **bd (beads)** for issue tracking. Run `bd prime` for workflow
context. Git hooks are already installed and tracked in `.beads/hooks/` — do **not** run
`bd hooks install`, which rewrites them and trips the pre-push dirty check.

**Quick reference:**

- `bd ready` - Find unblocked work
- `bd create "Title" --type task --priority 2` - Create issue
- `bd close <id>` - Complete work — **after the PR merges**, not before (see below)
- `task beads:pull` / `task beads:push` - Exchange issue data with the remote. The
  `pre-push` hook also runs the push (non-blocking, so check for its warning). A
  `post-merge` hook pulls, but git runs no post-merge hook on `git pull --rebase` — the
  workflow this project documents — so pull yourself after any pull.
- `task beads:check` - Warn about issues that shipped to `main` but are still open

The `.beads/issues.jsonl` export is local-only and gitignored (2026-08 security audit
finding F-SC1; the register is private, see `SECURITY.md`) — never commit it. Issue data
lives in the Dolt DB and travels over the Dolt remote instead.

### Closing issues: after merge, not at PR time

You are forbidden from merging, so an issue is still open at the moment its work
actually lands. Before opening a PR, run `task beads:push` and leave the issue
`in_progress`. Close it once the PR has merged:

```bash
bd close <id> && task beads:push
```

Reference issues in commit messages with a `Closes: cos-abcd` / `Refs: cos-abcd` trailer
— `task beads:check` uses those to spot work that shipped but stayed open.

For full workflow details: `bd prime`

## Tooling Policy

**Use `task <name>`** for all operations (run `task --list`). Fall back to `uv run` only
when no task exists. Never invoke `python` directly. This applies to beads too: prefer
`task beads:push` / `task beads:pull` over bare `bd dolt push` / `bd dolt pull`.

For `gh` subcommands without a task wrapper, direct invocation is fine.

### Gate Tasks

Deferred work, technical debt and TODOs to revisit later get a **gate task** in beads as
a dependency of the relevant work item.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->

<!-- END BEADS INTEGRATION -->
