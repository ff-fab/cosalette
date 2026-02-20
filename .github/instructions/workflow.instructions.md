---
description: 'Development workflow: Git flow, issue tracking, quality gates, session completion'
applyTo: '**'
---

# Workflow

## Git Workflow (GitHub Flow)

**CRITICAL: Never push directly to main. All changes go through PRs.**

1. **Create feature branch from main**

   ```bash
   git checkout main && git pull
   git checkout -b feature/description  # or fix/, docs/, chore/, etc.
   ```

2. **Make commits with clear messages** (conventional commits)

   ```bash
   git commit -m "feat: clear description of changes"
   # Prefixes: feat:, fix:, docs:, refactor:, chore:, test:
   ```

   **Conventional Commits are required.** They drive release automation:

   | Prefix   | SemVer effect | Example                           |
   | -------- | ------------- | --------------------------------- |
   | `feat:`  | MINOR bump    | `feat: add signal routing`        |
   | `fix:`   | PATCH bump    | `fix: correct timeout handling`   |
   | `feat!:` | MAJOR bump    | `feat!: redesign config schema`   |
   | `docs:`  | no release    | `docs: update setup guide`        |
   | `chore:` | no release    | `chore: bump dependencies`        |

3. **Ensure quality gates pass** before pushing — run `task pre-pr` or see
   [Pre-PR Quality Gate](#pre-pr-quality-gate) for details.

4. **Push and create pull request**

   ```bash
   git push -u origin feature/description
   gh pr create
   ```

5. **Wait for CI**

   ```bash
   task ci:wait -- <pr-number>   # polls until all checks complete
   ```

   **Always use `task ci:wait`** to wait for CI. Do not use `gh pr checks --watch`
   (opens alternate buffer, breaks agents) or ad-hoc polling loops.

   Never merge unless directly requested by the user.

**Key principle:** `main` is always deployable.

## Releases

If this project uses **Release Please**, releases are fully automated:

1. Push/merge to `main` with conventional commits.
2. Release Please opens/updates a release PR with changelog and version bump.
3. Merge the release PR to create a GitHub Release and SemVer tag (`vX.Y.Z`).

Agents do NOT manually create tags or releases — the bot handles it.

## Issue Tracking (Beads)

This project uses **bd (beads)** — a git-backed graph issue tracker for AI agents.
Issues are stored as JSONL in `.beads/` and committed to git.

Run `bd prime` for full workflow context.

**Quick reference:**

| Command                                      | Purpose                              |
| -------------------------------------------- | ------------------------------------ |
| `bd ready`                                   | Find unblocked work                  |
| `bd create "Title" --type task --priority 2` | Create issue                         |
| `bd update <id> --claim`                     | Claim a task (assigns + in_progress) |
| `bd close <id>`                              | Complete work                        |
| `bd dep add <child> <parent>`                | Add dependency                       |
| `bd sync`                                    | Export to JSONL (run at session end) |

**Workflow:** Check `bd ready` at session start. Claim work, implement, close when done.
Commit beads state (`bd sync && git add .beads/ && git commit`) before pushing.

### Beads vs TODO: Two Systems, Distinct Purposes

This project uses **two complementary tracking systems**. Do not conflate them.

| System           | Purpose            | Content type            | Location     |
| ---------------- | ------------------ | ----------------------- | ------------ |
| **Beads (`bd`)** | Work tracking      | Actionable tasks, epics | `.beads/`    |
| **TODO folder**  | Deferred decisions | Rich deliberation docs  | `docs/TODO/` |

**Beads** tracks _work_: things to build, fix, or ship. Items flow through
`ready → in_progress → closed`.

**TODO items** (T1–Tn) are _deliberation documents_ — deferred decisions, architectural
evaluations, and technical debt assessments. They contain structured options with
advantages/disadvantages, trade-offs, and ADR references. They are mini-ADRs-in-waiting,
not work items.

### Gate Tasks (Hybrid Bridge)

When a TODO item has a **phase trigger** (e.g., "revisit when building frontend
components"), create a **gate task** in beads that:

1. References the TODO item: `"Evaluate signal value type (T6, docs/TODO/)"`
2. Is added as a **dependency** of the first task that would be affected
3. Contains no decision logic itself — points to the TODO doc for full context

This enforces that deferred decisions are evaluated at the right point in the workflow,
without duplicating the rich deliberation content into beads.

**Rules:**

- **Date-triggered TODOs** (e.g., "Review date: June 2026") stay markdown-only. Beads
  has no calendar awareness.
- **Phase-triggered TODOs** get a gate task as a dependency of the relevant phase task
- **When creating a new TODO item**, always check whether it needs a gate task
- **When closing a gate task**, the outcome should be one of:
  - A new ADR (if the decision is significant)
  - An update to the existing TODO item marking it resolved
  - New beads tasks created from the decision

## Pre-PR Quality Gate

Run `task pre-pr` to execute all quality gates before creating a PR. This task runs
pre-commit + lint + typecheck + tests + coverage.

All must pass before pushing.

## Session Completion ("Landing the Plane")

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete
until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** — create beads tasks for anything unfinished
2. **Run quality gates** (if code changed) — `task pre-pr`
3. **Create showboat demo** (if significant code/config change, or when requested):

   ```bash
   showboat init docs/planning/demos/<branch-name>.md "<Title of Work>"
   showboat note docs/planning/demos/<branch-name>.md "What was done and why."
   showboat exec docs/planning/demos/<branch-name>.md bash "<proof command>"
   showboat verify docs/planning/demos/<branch-name>.md  # Must exit 0
   ```

   Skip for: documentation-only changes, beads-only changes, trivial formatting fixes.

4. **Close beads tasks and commit state**:

   ```bash
   bd close <id>
   bd sync
   git add .beads/ && git commit -m "chore: sync beads state"
   ```

5. **PUSH TO REMOTE** — this is MANDATORY:

   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```

6. **Create PR** (if new branch): `gh pr create`
7. **Clean up** — clear stashes, prune remote branches
8. **Verify** — all changes committed AND pushed
9. **Hand off** — provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push
- If push fails, resolve and retry until it succeeds
- Beads state MUST be committed before pushing — the pre-push hook will reject pushes
  with uncommitted `.beads/` changes

## Showboat Demos (Proof of Work)

**Showboat** creates executable demo documents that prove an agent's work. A markdown
file mixing commentary with executable code blocks and their captured output. Serves as
both documentation and reproducible proof (`showboat verify` re-runs all blocks and
confirms outputs match).

| Convention    | Value                            |
| ------------- | -------------------------------- |
| **Location**  | `docs/planning/demos/`           |
| **Filename**  | `<branch-name>.md`               |
| **Committed** | Yes — part of the PR             |
| **MkDocs**    | Excluded (not published to site) |
| **Verify**    | `showboat verify` must exit 0    |

**Key commands:** `init`, `note`, `exec`, `pop`, `verify`, `extract`

**Reference:** [Showboat README](https://github.com/simonw/showboat) — installed in
devcontainer via `uv tool install showboat`.

**Test notes:**

- Shared fixtures (in `tests/fixtures/`) should be used to avoid duplication
- Always ensure tests, fixtures, documentation, and features stay in sync
