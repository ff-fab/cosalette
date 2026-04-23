---
agent: agent
description: 'Review a pull request — fetches ALL reviewer feedback, CI results, and code changes, then provides structured analysis with parallel perspective reviews and actionable implementation options.'
model: Claude Sonnet 4.6 (copilot)
---

# PR Review

Run the pr-review skill to analyze all open (or specified) pull requests.

## Step 1: Fetch All Feedback

Run the deterministic data-collection script — this is mandatory and must not be
skipped or replaced with ad-hoc `gh` commands:

```bash
task pr:feedback -- [PR_NUMBER]
```

If a PR number was provided in the conversation, pass it as the argument. Otherwise
omit it to auto-detect the PR for the current branch.

**Important:** Use the JSON output directly — do **NOT** pipe through `jq` or transform
it. The script already curates and flattens the data. All person fields are named
`author` and contain plain string logins (not objects).

## Step 2: Analyze & Respond

Follow the full workflow defined in `.github/skills/pr-review/SKILL.md`:
- **Steps 2-3**: Read changed files for full context
- **Step 4**: Fan out to 4 perspective reviewer sub-agents in parallel (security, maintainability, performance, quality)
- **Step 5**: Present structured tabular output (PR summary, CI status, perspective summaries, findings table, deep dives, CI hints, implementation options)
- **Step 6**: After user selects an option, implement fixes → push → CI wait → ask before merge
