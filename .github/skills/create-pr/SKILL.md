---
name: create-pr
description: >
  Create a pull request using the project's PR template. Use when the user says "create a PR",
  "open a PR", "submit for review", "push and PR", or any variation. Also used by the
  orchestrator's pr-subagent. Expects changes to be committed and pushed already.
---

# Create Pull Request

Generate a PR title and body following the project template, then open PR.

## Prerequisites

Before creating the PR, verify:

1. **Not on main/master** — refuse to create a PR from the default branch.
2. **Changes are committed** — no uncommitted work.
3. **Branch is pushed** — `git push -u origin $(git branch --show-current)` if needed.
4. **No existing PR** — check with `gh pr view` first.

## PR Format

Project uses PR template `.github/PULL_REQUEST_TEMPLATE.md`.
Follow it exactly.

## Title Convention

Use the same conventional commit prefix as the branch/commits.

## Procedure

1. **Gather context** from `git log`, `git diff main`, branch name, and beads tasks.
2. **Write title** — derive from commits or branch name.
3. **Write body** — fill the template sections from the diff and commit messages.
   Keep it concise. Bullet points, not prose.
4. **Create PR**:
   ```bash
   gh pr create --title "<title>" --body "<body>"
   ```
5. **Report** the PR URL.

## Rules

- **Never merge** — only create. The user decides when to merge.
- **Never use `--fill`** — always provide explicit title and body.
- If quality gates haven't been run, warn the user but don't block.
- For trivial PRs (1-2 files, obvious change), omit "Key decisions" section.
