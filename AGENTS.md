# Agent Instructions

## Issue Tracking

This project uses **bd (beads)** for issue tracking. Run `bd prime` for workflow
context, or install hooks (`bd hooks install`) for auto-injection.

**Quick reference:**

- `bd ready` - Find unblocked work
- `bd create "Title" --type task --priority 2` - Create issue
- `bd close <id>` - Complete work
- `bd dolt push` - Push beads to remote

For full workflow details: `bd prime`

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

## GitHub Tooling Policy

- Use **GitHub CLI (`gh`)** and **git CLI** directly for PR/issue workflows.

Quick CLI equivalents:

```bash
gh pr view --json number,title,headRefName,baseRefName,state,url
gh pr checks
gh pr comment <number> --body "..."
gh pr review <number> --comment --body "..."
gh issue list --limit 50
```

## Commit Convention

All commits **must** follow
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope]: <description>
```

Common prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.

Breaking changes: add `!` after the type (e.g., `feat!: redesign config`).

These prefixes drive automated release versioning (if Release Please is enabled).

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete
until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Close beads tasks and commit** - Beads state MUST be committed before pushing:
   ```bash
   bd close <id>                # Close finished work
   bd sync                      # Export to JSONL
   git add .beads/ && git commit -m "chore: sync beads state"
   ```
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Create PR** (if new branch):
   ```bash
   gh pr create
   ```
6. **Wait for CI** (if PR exists):
   ```bash
   task ci:wait -- <pr-number>   # polls until all checks complete
   ```
   **Always use `task ci:wait`** — do not use `gh pr checks --watch` (opens alternate
   buffer, breaks agents) or ad-hoc polling loops.
7. **Clean up** - Clear stashes, prune remote branches
8. **Verify** - All changes committed AND pushed
9. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
- Beads state MUST be committed before pushing — the pre-push hook will reject pushes
  with uncommitted `.beads/` changes

### Gate Tasks

Deferred work, technical debt and TODOs to revisit later get a **gate task** in beads as
a dependency of the relevant work item.

## Showboat Demos

Showboat demos are executable markdown documents that mix commentary with code blocks
and their captured output — serving as both documentation and reproducible proof of
work. They are **opt-in**: create one when requested by the user, or suggest one after
significant code changes. See the `showboat-demo` skill for the full workflow and
conventions.

<!-- BEGIN BEADS INTEGRATION -->
<!-- END BEADS INTEGRATION -->
