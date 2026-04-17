---
description: PR subagent — creates pull requests using the project template, delegated by the orchestrator
argument-hint: Branch name, summary of changes, beads task IDs, and quality gate results from the orchestrator
tools: ['execute/runInTerminal', 'execute/getTerminalOutput', 'read', 'search']
model: Claude Sonnet 4 (copilot)
---

Create a pull request for the current branch using the project's PR template and skill.

Load the create-pr skill (@file:.github/skills/create-pr/SKILL.md) and follow its
procedure exactly.

**Input from orchestrator:**
- Branch name and summary of changes
- Beads task IDs to reference
- Quality gate results (test count, lint/typecheck status)
- Any key design decisions worth noting

**Output contract:** Return the PR URL and number as a brief message.

**Rules:**
- Never merge the PR — only create it.
- Never use `gh pr create --fill` — always provide explicit title and body.
- If the branch isn't pushed yet, push it first.
- If a PR already exists for this branch, return the existing PR URL instead.
