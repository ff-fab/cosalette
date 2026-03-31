# CLAUDE.md

This project's conventions are documented in GitHub Copilot instruction files. Read and
follow them.

## Instructions

- [.github/copilot-instructions.md](.github/copilot-instructions.md) — Project overview,
  workflow, code quality, PR policy
- [.github/instructions/tooling.instructions.md](.github/instructions/tooling.instructions.md)
  — Use `task` and `uv`, never bare `python`
- [.github/instructions/workflow.instructions.md](.github/instructions/workflow.instructions.md)
  — Git flow, conventional commits, beads issue tracking, session completion
- [.github/instructions/testing-python.instructions.md](.github/instructions/testing-python.instructions.md)
  — pytest patterns, AAA, ISTQB techniques
- [.github/instructions/documentation.instructions.md](.github/instructions/documentation.instructions.md)
  — Zensical site generator, ADR format

## Skills

Reusable workflow definitions available as slash commands in Claude Code (`/pr-review`,
`/pre-pr-gate`, `/showboat-demo`) and as Copilot skills. The canonical body lives in
`.github/skills/`; `.claude/skills/` adds Claude Code metadata (`allowed-tools`).

- **pr-review** — Fetch all PR feedback via
  [fetch-pr-feedback.sh](.github/skills/pr-review/fetch-pr-feedback.sh), then analyze
  CI, review comments, and code quality
- **pre-pr-gate** — End-of-session workflow: `task pre-pr`, close beads, push, create PR
- **showboat-demo** — Create reproducible proof-of-work demos with `showboat`

## Key Rules

- **Never push directly to `main`.** All changes go through PRs.
- **Never merge a PR** unless the user explicitly asks.
- **Conventional Commits required** (`feat:`, `fix:`, `docs:`, `chore:`, etc.).
- **Use `task <name>`** for all operations (run `task --list`). Fall back to `uv run`
  only when no task exists. Never invoke `python` directly.
- **ADRs** live in `docs/adr/`. Follow existing decisions; create new ADRs for major
  changes.
- **Beads (`bd`)** for issue tracking. Run `bd prime` for full context.
