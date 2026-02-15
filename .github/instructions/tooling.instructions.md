---
description: 'Tooling policy: task runner and uv, never bare python'
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
| Run a specific test file      | `task test:file -- tests/unit/test_foo.py`   |
| Run tests matching a pattern  | `task test:file -- -k test_my_function`      |
| Run tests with coverage       | `task test:cov`                              |
| Lint (ruff check + format)    | `task lint`                                  |
| Fix lint issues               | `task lint:fix`                              |
| Type check (mypy)             | `task typecheck`                             |
| All checks (lint+type+test)   | `task check`                                 |
| Pre-PR quality gate           | `task pre-pr`                                |
| Wait for CI on a PR           | `task ci:wait -- <pr-number>`                |
| Preview docs                  | `task docs:serve`                            |
| Sync dependencies             | `task sync`                                  |

## When no task exists

For one-off commands not covered by the Taskfile, use `uv run`:

```bash
# Correct
uv run pytest tests/unit/test_foo.py -v
uv run mypy src/cosalette/_health.py
uv run ruff check src/

# WRONG — never do this
python -m pytest tests/unit/test_foo.py -v
python tests/scripts/summarize_tests.py
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
