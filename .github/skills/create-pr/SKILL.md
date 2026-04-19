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
4. **No existing PR** — check with `gh pr view --json url,number 2>/dev/null`.
   If it returns data, a PR already exists — report the existing URL and stop.
   If it exits non-zero (no PR), proceed.

## PR Format

Project uses PR template `.github/pull_request_template.md`.
Follow it exactly.

## Title Convention

Use the same conventional commit prefix as the branch/commits.

## Procedure

1. **Gather context** from `git log`, `git diff main`, branch name, and beads tasks.
2. **Write title** — derive from commits or branch name.
3. **Write body** — fill the template sections from the diff and commit messages.
   Keep it concise. Bullet points, not prose.
4. **Create PR** — use `create_file` to write the body, then a single `gh` command:
   ```
   # Step A: use the create_file tool to write the rendered body
   create_file("/tmp/pr-body.md", "<rendered body>")

   # Step B: single terminal command — no heredocs, no compound commands
   gh pr create --title "<title>" --body-file /tmp/pr-body.md && rm -f /tmp/pr-body.md
   ```
   **Why not a heredoc?** Shell heredocs inside `run_in_terminal` cause the
   terminal to show partial output while `gh` displays a progress spinner.
   The agent misinterprets the spinner as a hang, sends a new command (which
   `^C`s the running process), then discovers the PR was already created.
   Writing the file via `create_file` avoids this entirely.
5. **Report** the PR URL.

## Rules

- **Never merge** — only create. The user decides when to merge.
- **Always provide explicit title and body** — do not rely on `--fill`.
- If quality gates haven't been run, warn the user but don't block.
- For trivial PRs (1-2 files, obvious change), omit "Key decisions" section.

## Scope Boundary

This skill handles **PR creation only**. For the full pre-PR workflow (quality
gates → beads sync → push), use the `pre-pr-gate` skill first, then this skill
to create the PR.
