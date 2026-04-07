# GitHub Copilot Instructions

## Project Overview

**cosalette** - a Python project.

## Workflow

- **Branching:** GitHub Flow — branch from `main`, open PR, squash-merge.
- **Commits:** Conventional Commits required (`feat:`, `fix:`, `docs:`, `chore:`, etc.).
- **Releases:** Automated via Release Please (SemVer tags).
- **Never push directly to `main`.**

## Pull Request & Merge Policy

**NEVER merge a pull request unless the user explicitly asks you to merge.**

Your job ends at creating the PR and waiting for CI. The human reviewer decides when to
merge. Even if all CI checks pass and the code looks perfect — do NOT merge. Do NOT
approve-and-merge. Do NOT enable auto-merge. Wait for an explicit user instruction like
"merge this", "go ahead and merge", or "land it".

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

## Architecture Decision Records

All major decisions are documented in `docs/adr/`. **Follow these decisions.**

**Do not write ADR Markdown directly.** Use the `adr-create` skill
(`.github/skills/adr-create/SKILL.md`) which produces schema-validated JSON and
renders canonical Markdown via `task adr:create`. See
`.github/agents/schemas/adr-input.schema.json` for the input schema.
