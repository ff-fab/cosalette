# Agent Instructions

## Issue Tracking

This project uses **bd (beads)** for issue tracking. Run `bd prime` for workflow
context, or install hooks (`bd hooks install`) for auto-injection.

**Quick reference:**

- `bd ready` - Find unblocked work
- `bd create "Title" --type task --priority 2` - Create issue
- `bd close <id>` - Complete work
- `bd dolt push` - Push Dolt DB to remote (if configured)

For full workflow details: `bd prime`

### Multi-Machine / New-Clone Setup

**Important:** Beads uses a local Dolt database that is **not** auto-refreshed by
`git pull`. When you clone this repo or pull changes made on a different machine you
must manually import the tracked issue export into the local DB.

**Fresh clone (first time on a machine):**

```bash
bd bootstrap   # auto-imports from .beads/issues.jsonl; run once after clone
```

`bd bootstrap` is a no-op if a local database already exists — call it before any other
`bd` command on a new clone.

**Existing clone that is out of date (e.g. after pulling updates from another
machine):**

```bash
bd import      # upserts .beads/issues.jsonl into the local Dolt DB
```

If `bd doctor` also reports _"Repo Fingerprint: Database belongs to different
repository"_ (fingerprint mismatch after URL/machine change), run:

```bash
bd migrate --update-repo-id --yes   # fix stored repo hash, then:
bd import                           # re-sync tracked JSONL into Dolt DB
```

**Symptoms of a stale local DB:**

- `bd list` / `bd status` shows fewer issues or different open/closed counts than
  expected.
- `bd doctor` reports a repo fingerprint mismatch.
- `.beads/sync-state.json` shows `"needs_manual_sync": true`.

**Backup 401 parse error:** If `bd` commands emit
`Warning: auto-backup failed: … strconv.ParseUint: parsing "401\n": invalid syntax`, the
local Dolt backup directory has a stale manifest written by a different Dolt version.
Fix:

```bash
rm -rf .beads/backup && mkdir -p .beads/backup   # clears corrupt manifest
# Dolt auto-backup will recreate the directory on the next write
```

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

## GitHub Tooling Policy

Use **task wrappers** when available instead of bare `gh`:

- `task pr:diff -- <n>` instead of `gh pr diff <n>`
- `task pr:feedback -- <n>` instead of running the feedback script directly
- `task ci:wait -- <n>` instead of `gh pr checks <n>`

For `gh` subcommands without a task wrapper, direct invocation is fine:

```bash
gh pr create
gh pr view --json number,title,headRefName,baseRefName,state,url
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
   task beads:sync              # Export DB to .beads/issues.jsonl
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

   **STOP HERE.** Do NOT merge the PR. The human reviewer decides when to merge. Never
   approve-and-merge, never enable auto-merge — even if all checks pass.

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
- NEVER merge a PR unless the user explicitly requests it

### Gate Tasks

Deferred work, technical debt and TODOs to revisit later get a **gate task** in beads as
a dependency of the relevant work item.

## Showboat Demos

Showboat demos are executable markdown documents that mix commentary with code blocks
and their captured output — serving as both documentation and reproducible proof of
work. They are **opt-in**: create one when requested by the user, or suggest one after
significant code changes. See the `showboat-demo` skill for the full workflow and
conventions.

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
