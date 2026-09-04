# Beads - AI-Native Issue Tracking

Welcome to Beads! This repository uses **Beads** for issue tracking - a modern,
AI-native tool designed to live directly in your codebase alongside your code.

## What is Beads?

Beads is issue tracking that lives in your repo, making it perfect for AI coding agents
and developers who want their issues close to their code. No web UI required -
everything works through the CLI and integrates seamlessly with git.

**Learn more:** [github.com/steveyegge/beads](https://github.com/steveyegge/beads)

## Quick Start

### Essential Commands

```bash
# Create new issues
bd create "Add user authentication"

# View all issues
bd list

# View issue details
bd show <issue-id>

# Update issue status
bd update <issue-id> --status in_progress
bd update <issue-id> --status done

# Exchange issue data with the remote
bd dolt pull    # or: task beads:pull  — after `git pull`
bd dolt push    # or: task beads:push  — before `git push`
```

### Working with Issues

Issues in Beads are:

- **Repo-local**: The Dolt database lives in `.beads/dolt/`, right next to your code
- **AI-friendly**: CLI-first design works perfectly with AI coding agents
- **Branch-aware**: Issues can follow your branch workflow
- **Replicated over the Dolt remote**: `task beads:push` / `task beads:pull` against the
  GitHub `origin`. The ref lives on the **remote only**
  (`git ls-remote origin 'refs/dolt/*'`); a local `git for-each-ref refs/dolt` is empty
  by design

> **In this repository, issue data is NOT committed to git.** The Dolt DB is the single
> source of truth. `bd export` (via `task beads:sync`) writes `.beads/issues.jsonl` as a
> **local-only** snapshot for inspection and diffing; it is generated output, never an
> input, and it is gitignored and untracked so that a large regenerated file stays out
> of PR diffs and there is no second, divergent copy of the issue data. Never
> `git add .beads/issues.jsonl`. Only `config.yaml`, `metadata.json`, `hooks/`,
> `README.md` and `.gitignore` are tracked.

## Why Beads?

✨ **AI-Native Design**

- Built specifically for AI-assisted development workflows
- CLI-first interface works seamlessly with AI coding agents
- No context switching to web UIs

🚀 **Developer Focused**

- Issues live in your repo, right next to your code
- Works offline, syncs when you push
- Fast, lightweight, and stays out of your way

🔧 **Git Integration**

- Replication tied to git operations: the `pre-push` hook runs `bd dolt push`
  (non-blocking) and the `post-merge` hook runs `bd dolt pull`. Note git runs **no**
  post-merge hook on `git pull --rebase` — the workflow this project documents — so the
  pull is a safety net, not the mechanism: run `task beads:pull` yourself
- Branch-aware issue tracking
- Dolt-native merge resolution

## Get Started with Beads

Try Beads in your own projects:

```bash
# Install Beads
curl -sSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash

# Initialize in your repo
bd init

# Create your first issue
bd create "Try out Beads"
```

## Learn More

- **Documentation**:
  [github.com/steveyegge/beads/docs](https://github.com/steveyegge/beads/tree/main/docs)
- **Quick Start Guide**: Run `bd quickstart`
- **Examples**:
  [github.com/steveyegge/beads/examples](https://github.com/steveyegge/beads/tree/main/examples)

---

_Beads: Issue tracking that moves at the speed of thought_ ⚡
