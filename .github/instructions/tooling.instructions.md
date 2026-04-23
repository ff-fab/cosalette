---
description: 'Tooling policy: task runner, uv, and gh CLI wrappers'
applyTo: '**'
---

# Tooling Instructions

## Golden Rule

**Never invoke `python` or `python -m` directly.** All commands go through either:

1. **`task <name>`** — preferred, uses the Taskfile.yml definitions
2. **`uv run <command>`** — fallback when no task exists

## Task Commands (use these first)

Run `task --list` to see all available tasks. Key tasks for development:

| Need                          | Command                                      |
| ----------------------------- | -------------------------------------------- |
| Run all unit tests            | `task test:unit`                             |
| Run integration tests         | `task test:integration`                      |
| Run a specific test file      | `task test:file -- tests/unit/test_foo.py`   |
| Run tests matching a pattern  | `task test:file -- -k test_my_function`      |
| Run tests with coverage       | `task test:cov`                                |
| Lint (ruff check + format)    | `task lint`                                    |
| Fix lint issues               | `task lint:fix`                                |
| Type check (ty)               | `task typecheck`                               |
| All checks (lint+type+test)   | `task check`                                   |
| Complexity (all gates)        | `task complexity`                              |
| Cyclomatic complexity only    | `task complexity:cyclomatic`                   |
| Cognitive complexity only     | `task complexity:cognitive`                    |
| Duplication detection         | `task similarity`                              |
| Pre-PR quality gate           | `task pre-pr`                                  |
| Create a PR                   | `task pr:create -- --title "..." --body "..."` |
| Wait for CI on a PR           | `task ci:wait -- <pr-number>`                  |
| Show PR diff                  | `task pr:diff -- <pr-number>`                  |
| Fetch all PR feedback (JSON)  | `task pr:feedback -- <pr-number>`              |
| List open PRs (no releases)   | `task pr:list`                                 |
| Preview docs                  | `task docs:serve`                              |
| Sync dependencies             | `task sync`                                    |
| Export beads DB to JSONL      | `task beads:sync`                              |

## GitHub CLI wrapper policy

**Never invoke `gh` directly for commands that have task wrappers.** Use the wrapper
instead:

- `task pr:create -- --title "..." --body "..."` instead of `gh pr create ...`
- `task pr:diff -- <n>` instead of `gh pr diff <n>`
- `task pr:feedback -- <n>` instead of `bash .github/skills/pr-review/fetch-pr-feedback.sh <n>`
- `task ci:wait -- <n>` instead of `gh pr checks <n>`

For `gh` subcommands without a task wrapper (e.g., `gh issue list`),
direct invocation is acceptable.

These wrappers are convenience aliases — they do not add input validation or
sanitization.

## When no task exists

For one-off commands not covered by Taskfile, use `uv run` from the repo root:

```bash
# Correct
uv run pytest packages/tests/unit/test_foo.py -v
uv run ty check packages/src/cosalette/_health.py
uv run ruff check packages/src/

# WRONG — never do this
python -m pytest packages/tests/unit/test_foo.py -v
python packages/tests/scripts/summarize_tests.py
```

## Why

- `uv run` ensures the correct virtual environment and dependencies are used
- `task` commands encode project conventions (flags, working directory, coverage
  thresholds) so agents don't need to remember them
- Bare `python` may pick up the wrong interpreter or miss dependencies

## Suggesting Taskfile changes

If you find yourself repeatedly needing a command pattern that doesn't have a task,
**propose adding it to `Taskfile.yml`** rather than using raw `uv run` calls. This is
preferred because tasks encode conventions once and all agents/developers benefit.
