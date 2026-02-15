# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## GitHub Tooling Policy

- Use **GitHub CLI (`gh`)** and **git CLI** directly for PR/issue workflows.
- Do **not** rely on GitKraken MCP tools in this repository.
- If an agent attempts GitKraken MCP and authentication is missing, switch immediately
  to `gh` commands.

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

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

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

## Beads vs TODO: Two Systems, Distinct Purposes

| System           | Purpose            | Content type            | Location     |
| ---------------- | ------------------ | ----------------------- | ------------ |
| **Beads (`bd`)** | Work tracking      | Actionable tasks, epics | `.beads/`    |
| **TODO folder**  | Deferred decisions | Rich deliberation docs  | `docs/TODO/` |

**Beads** tracks _work_: things to build, fix, or ship.

**TODO items** (T1–Tn) are _deliberation documents_ — deferred decisions, architectural
evaluations, and technical debt. They are mini-ADRs-in-waiting.

### Gate Tasks

Phase-triggered TODOs get a **gate task** in beads as a dependency of the relevant work
item. The gate task references the TODO doc but contains no decision logic itself.

- Date-triggered TODOs stay markdown-only
- When closing a gate task: create an ADR, update the TODO, or create new tasks

## Showboat Demos (Proof of Work)

**Showboat** creates executable demo documents that prove an agent's work. Every session
that changes code or configuration MUST produce a showboat demo before pushing.

### What is a Showboat Demo?

A markdown file that mixes commentary with executable code blocks and their captured
output. The demo serves as both:

- **Documentation** — what was changed and why
- **Reproducible proof** — `showboat verify` re-runs all code blocks and confirms
  outputs match

### When to Create a Demo

- **Required:** Any session that changes code, configuration, or infrastructure
- **Skip:** Documentation-only changes, beads-only changes, trivial formatting fixes

### How to Create a Demo

```bash
# 1. Initialize the demo (use the branch name as filename)
showboat init docs/demos/<branch-name>.md "<Title describing the work>"

# 2. Add commentary explaining what was done
showboat note docs/demos/<branch-name>.md "Describe the change and its purpose."

# 3. Run commands that prove it works (output is captured automatically)
showboat exec docs/demos/<branch-name>.md bash "<test or verification command>"

# 4. If a command fails, remove it and redo
showboat pop docs/demos/<branch-name>.md
showboat exec docs/demos/<branch-name>.md bash "<corrected command>"

# 5. Verify the demo is reproducible (MUST exit 0)
showboat verify docs/demos/<branch-name>.md
```

### Demo Content Guidelines

The agent decides the scope based on work complexity:

- **Simple fix:** Note explaining the fix + one `exec` proving the test passes
- **New feature:** Notes on design choices + multiple `exec` blocks showing the feature
  works (API responses, test runs, etc.)
- **Refactoring:** Before/after notes + proof that tests still pass

### Conventions

| Convention    | Value                            |
| ------------- | -------------------------------- |
| **Location**  | `docs/demos/`                    |
| **Filename**  | `<branch-name>.md`               |
| **Committed** | Yes — part of the PR             |
| **MkDocs**    | Excluded (not published to site) |
| **Verify**    | `showboat verify` must exit 0    |

### Reference

- [Showboat README](https://github.com/simonw/showboat)
- Installed in devcontainer via `uv tool install showboat`
- Key commands: `init`, `note`, `exec`, `pop`, `verify`, `extract`
