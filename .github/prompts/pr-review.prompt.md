---
agent: agent
description: 'Review a pull request — fetches ALL reviewer feedback, CI results, and code changes, then provides structured analysis with parallel perspective reviews and actionable implementation options.'
---

# PR Review

Run the pr-review skill to analyze the current (or specified) pull request.

## Step 1: Fetch All Feedback

Run the deterministic data-collection script — this is mandatory and must not be
skipped or replaced with ad-hoc `gh` commands:

```bash
bash .github/skills/pr-review/fetch-pr-feedback.sh [PR_NUMBER]
```

If a PR number was provided in the conversation, pass it as the argument. Otherwise
omit it to auto-detect the PR for the current branch.

## Step 2: Analyze & Respond

Follow the full workflow defined in `.github/skills/pr-review/SKILL.md`:
- **Steps 2-3**: Read changed files for full context
- **Step 4**: Fan out to 4 perspective reviewer sub-agents in parallel (security, maintainability, performance, quality)
- **Step 5**: Present structured tabular output (PR summary, CI status, perspective summaries, findings table, deep dives, CI hints, implementation options)
- **Step 6**: After user selects an option, implement fixes → push → CI wait → ask before merge
