---
name: showboat-demo
description:
  Create a showboat demo — an executable proof-of-work document. Use when the user asks for a demo, says "showboat this", "prove it works", "create a demo", or when you want to suggest documenting significant work with reproducible proof.
---

# Showboat Demo

[Showboat](https://github.com/simonw/showboat) creates executable demo documents — markdown files
that mix commentary with executable code blocks and their captured output. A demo serves as both:

- **Documentation** — what was changed and why
- **Reproducible proof** — `showboat verify` re-runs all code blocks and confirms outputs match

## When to Create a Demo

Create a showboat demo when:

- The **user explicitly requests** one
- You want to **suggest** documenting a significant change (ask first!)

Do NOT create demos automatically. Demos are opt-in.

## Workflow

```bash
# 1. Initialize (use the branch name as filename)
showboat init docs/planning/demos/<branch-name>.md "<Title describing the work>"

# 2. Add commentary explaining what was done
showboat note docs/planning/demos/<branch-name>.md "Describe the change and its purpose."

# 3. Run commands that prove it works (output is captured automatically)
showboat exec docs/planning/demos/<branch-name>.md bash "<test or verification command>"

# 4. If a command fails, remove it and redo
showboat pop docs/planning/demos/<branch-name>.md
showboat exec docs/planning/demos/<branch-name>.md bash "<corrected command>"

# 5. Verify the demo is reproducible (MUST exit 0)
showboat verify docs/planning/demos/<branch-name>.md
```

## Scoping Guidelines

The agent decides scope based on work complexity:

- **Simple fix:** Note explaining the fix + one `exec` proving the test passes
- **New feature:** Notes on design choices + multiple `exec` blocks showing the feature works
- **Refactoring:** Before/after notes + proof that tests still pass

## Conventions

| Convention    | Value                            |
| ------------- | -------------------------------- |
| **Location**  | `docs/planning/demos/`           |
| **Filename**  | `<branch-name>.md`               |
| **Committed** | Yes — part of the PR             |
| **Zensical**  | Excluded (not published to site) |
| **Verify**    | `showboat verify` must exit 0    |

## Reference

- Installed in devcontainer via `uv tool install showboat`
- Key commands: `init`, `note`, `exec`, `pop`, `verify`, `extract`
- Run `showboat --help` for full CLI reference
