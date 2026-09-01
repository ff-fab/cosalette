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
- **Replicated over the Dolt remote**: `bd dolt push` / `bd dolt pull` against the
  GitHub `origin` (refs under `refs/dolt/*`)

> **In this repository, issue data is NOT committed to git.** `bd export` (via
> `task beads:sync`) writes `.beads/issues.jsonl` as a **local-only** snapshot for
> inspection and diffing; it is gitignored and untracked following the 2026-08 security
> audit (F-SC1 / CWE-359 — the export embeds a maintainer's personal email). Never
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
  (non-blocking) and the `post-merge` hook runs `bd dolt pull`
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
