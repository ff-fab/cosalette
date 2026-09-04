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

## Key Rules

- **Never merge a PR** unless the user explicitly asks.
- **Use `task <name>`** for all operations (run `task --list`). Fall back to `uv run`
  only when no task exists. Never invoke `python` directly.
- **ADRs** live in `docs/adr/`. Follow existing decisions. **Do not write ADR Markdown
  directly** — use the `adr-create` skill (`task adr:create`).
- **Beads (`bd`)** for issue tracking. Run `bd prime` for full context. Issue data lives
  in the Dolt DB and is shared via `task beads:push` / `task beads:pull`, never
  committed. The exchange ref lives on the **remote only** —
  `git ls-remote origin 'refs/dolt/*'` shows it, while a local
  `git for-each-ref refs/dolt` is empty by design, so that is not a sign sync is broken.
  Verify sync with `bd doctor` (`Sync Staleness`) or `bd dolt remote list`; ignore
  `bd dolt show`, which misreports `Remotes: (none)`. `.beads/issues.jsonl` is a
  local-only export — generated output, never an input — gitignored and untracked so a
  large regenerated file stays out of PR diffs and there is no second, divergent copy of
  the issue data. Never `git add` it or re-track it.
- **Close beads issues after the PR merges**, not when you open it — you are not allowed
  to merge, so the work is not done at PR time. `task beads:check` warns about issues
  that shipped to `main` but stayed open.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->

<!-- END BEADS INTEGRATION -->
