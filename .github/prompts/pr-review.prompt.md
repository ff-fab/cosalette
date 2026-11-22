---
agent: agent
description: 'Review a pull request — fetches ALL reviewer feedback, CI results, and code changes, then provides structured analysis with parallel perspective reviews and actionable implementation options.'
---

# PR Review

Read `.github/skills/pr-review/SKILL.md` and follow it exactly, Steps 1–7. It is the
single source of truth for this workflow; this prompt only exposes it to GitHub
Copilot (Claude Code and Codex load the skill directly).

- **PR number given** in the conversation: review that PR (Mode A).
- **No PR number**: review every open PR except release-please (Mode B).

Step 2 (`task pr:feedback -- <PR_NUMBER>`) is mandatory and must not be replaced with
ad-hoc `gh` commands.
