---
agent: agent
description: 'Build a linear walkthrough of the codebase (or a specific module) using showboat. Produces a documented, reproducible tour with inline code snippets and commentary.'
---

# Code Walkthrough with Showboat

Build a linear, narrative walkthrough of the codebase using showboat. The result is a
self-contained markdown document that explains how the code works, with real code
snippets pulled from the source as reproducible proof.

## Step 1: Learn Showboat

Run `showboat --help` to understand the available commands and options.

## Step 2: Determine Scope

If the user specified a module, package, or area — focus on that. Otherwise, build a
full project walkthrough.

1. **Map the project structure:**

   ```bash
   find . -type d \( -name '.venv' -o -name '.git' -o -name 'node_modules' -o -name '__pycache__' \) -prune -false -o -type f -name '*.py' | head -60
   find . -maxdepth 3 \( -name '__pycache__' -o -name '.git' -o -name 'node_modules' -o -name '.venv' \) -prune -o -print | sed 's|^\./||' | sort
   ```

2. **Identify the entry points** — `main.py`, `__init__.py`, `config.py`, CLI entry
   points, etc.

3. **Read the source files** to understand the architecture. Start from entry points and
   follow the call chain.

4. **Plan the walkthrough order** — a logical reading order, not alphabetical. Typical
   flow:
   - Project overview and structure
   - Configuration / settings
   - Core domain types and models
   - Main logic / entry points
   - Supporting utilities
   - Testing strategy and fixtures

## Step 3: Build the Walkthrough

Initialize the showboat document. Walkthroughs live in `docs/planning/` (not
`docs/planning/demos/`) because they are standalone reference documents, not
branch-specific proof-of-work artifacts.

```bash
showboat init docs/planning/walkthrough.md "Code Walkthrough: <Project Name>"
```

For each section of the walkthrough:

1. **Add a narrative note** explaining the purpose and design of the module:

   ```bash
   showboat note docs/planning/walkthrough.md "## Section Title\n\nExplanation of what this module does, why it exists, and how it fits into the architecture."
   ```

2. **Include code snippets** using `showboat exec` with `sed`, `grep`, `awk`, or `cat`
   to extract the relevant portions of source:

   ```bash
   # Show a specific function or class
   showboat exec docs/planning/walkthrough.md bash "sed -n '10,35p' packages/src/<module_name>/core.py"

   # Show a key pattern with grep context
   showboat exec docs/planning/walkthrough.md bash "grep -n -A 10 'class MyClass' packages/src/<module_name>/core.py"

   # Show a whole short file
   showboat exec docs/planning/walkthrough.md bash "cat packages/src/<module_name>/config.py"
   ```

3. **Add connecting commentary** between snippets, explaining:
   - Why this pattern was chosen
   - How this piece connects to the next
   - Interesting design decisions, trade-offs, or gotchas
   - References to PEPs, design patterns, or principles where relevant

4. **If an exec block fails**, remove it with `showboat pop` and retry.

### Snippet Extraction Tips

- `sed -n 'START,ENDp' FILE` — extract a line range (best for functions/classes)
- `grep -n -B 2 -A 15 'PATTERN' FILE` — find and show context around a pattern
- `awk '/^class MyClass/,/^class |^def [a-z]/' FILE` — extract a full class
- `head -n 30 FILE` — show the top of a file (imports, module docstring)
- Prefer `sed -n` for precise ranges once you know the line numbers
- Use `grep -n` first to discover line numbers, then `sed -n` to extract

## Step 4: Verify

The walkthrough must be reproducible:

```bash
showboat verify docs/planning/walkthrough.md
```

All `exec` blocks must produce the same output on re-run. If verification fails, fix
the failing block (`showboat pop` + re-add).

## Step 5: Commit

```bash
git add docs/planning/walkthrough.md
git commit -m "docs: add code walkthrough"
```

## Output Quality

A good walkthrough:

- **Tells a story** — reads linearly, each section building on the previous
- **Shows real code** — not paraphrased, pulled from source via exec blocks
- **Explains the "why"** — not just what the code does, but why it was designed that way
- **Is reproducible** — `showboat verify` passes, so the snippets stay in sync with code
- **Highlights patterns** — names design patterns, SOLID principles, and idioms used
