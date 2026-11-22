---
agent: agent
description: 'Review a pull request — fetches ALL reviewer feedback, CI results, and code changes, then provides structured analysis with parallel perspective reviews and actionable implementation options.'
---

# PR Review

Read `.github/skills/pr-review/SKILL.md` and follow it exactly, passing along any PR
number the user gave. The skill is the single source of truth for this workflow; this
prompt only exposes it to GitHub Copilot (Claude Code and Codex load the skill
directly).
