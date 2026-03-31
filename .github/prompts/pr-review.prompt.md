---
agent: agent
description: 'Review a pull request — fetches ALL reviewer feedback (inline comments, reviews, conversation) and CI status, then provides actionable analysis with teaching context.'
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

Follow the full analysis workflow defined in `.github/skills/pr-review/SKILL.md`,
starting from **Step 2** (reading changed files) onward.
