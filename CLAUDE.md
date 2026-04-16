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
- **Beads (`bd`)** for issue tracking. Run `bd prime` for full context.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->

## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow
context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO
  lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete
until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
