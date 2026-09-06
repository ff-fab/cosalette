# GitHub Copilot Instructions

## Project Overview

**cosalette** - a Python project.

## Workflow

- **Branching:** GitHub Flow — branch from `main`, open PR, squash-merge.
- **Commits:** Conventional Commits required (`feat:`, `fix:`, `docs:`, `chore:`, etc.).
- **Releases:** Automated via Release Please (SemVer tags).
  bump-patch-for-minor-pre-major: only breaking changes bump the minor, everything else
  (including non-breaking feat) bumps the patch until 1.0.0
- **Never push directly to `main`.**

## Pull Request & Merge Policy

**NEVER merge a pull request unless the user explicitly asks you to merge.**

Your job ends at creating the PR and waiting for CI. The human reviewer decides when to
merge. Even if all CI checks pass and the code looks perfect — do NOT merge. Do NOT
approve-and-merge. Do NOT enable auto-merge. Wait for an explicit user instruction like
"merge this", "go ahead and merge", or "land it".

## Issue Tracking (Beads)

Issues are tracked with **bd (beads)**, not GitHub Issues. Run `bd prime` for context.

- Issue data lives in a **Dolt database** (`.beads/dolt/`) and is exchanged with
  `task beads:push` / `task beads:pull`. It is **never committed to git**.
- `.beads/issues.jsonl` is a local-only, gitignored export — generated output, never an
  input. Keeping it untracked keeps a large regenerated file out of PR diffs and avoids
  a second, divergent copy of the issue data. Never `git add` it.
- **Close issues after the PR merges**, not when you open it — see the merge policy
  above: you never merge, so the work is not done at PR-creation time. Before pushing,
  run `task beads:push` and leave the issue `in_progress`; afterwards run
  `bd close <id> && task beads:push`.
- `task beads:check` warns when an open issue is already referenced by work merged
  to `main`.
- Reference issues in commits with a `Closes: cos-abcd` / `Refs: cos-abcd` trailer.

Full details: [`instructions/workflow.instructions.md`](instructions/workflow.instructions.md).

## Code Quality Principles

- **Brevity is a feature.** If you wrote 200 lines and it could be 50, rewrite it.
- **Simplicity test:** Ask yourself — "Would a senior engineer say this is
  overcomplicated?" If yes, simplify before submitting.
- Prefer clear, idiomatic code over clever abstractions.
- Every line should earn its place — remove dead code, redundant comments, and
  unnecessary indirection.

## GitHub Operations

- Use **task wrappers** when available (`task pr:diff`, `task pr:feedback`,
  `task ci:wait`). For `gh` subcommands without a wrapper, use `gh` directly.
- Prefer **`git` CLI** for version control operations.
- Do not depend on GitKraken MCP authentication in this repository.
- See `tooling.instructions.md` for the full wrapper policy.

## Library & API Documentation

Project has **Context7 MCP** configured. When you need docs for any library, framework, or API — use Context7 automatically instead of relying on training data. Applies to code generation, debugging, and review tasks.

Do not ask the user whether to use Context7; just invoke it when library context would improve accuracy.

## Architecture Decision Records

All major decisions are documented in `docs/adr/`. **Follow these decisions.**

**Do not write ADR Markdown directly.** Use the `adr-create` skill
(`.github/skills/adr-create/SKILL.md`) which produces schema-validated JSON and
renders canonical Markdown via `task adr:create`. See
`.github/agents/schemas/adr-input.schema.json` for the input schema.
