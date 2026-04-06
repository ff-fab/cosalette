---
name: pr-review
user-invocable: false
disable-model-invocation: true
description:
  Review open pull requests — fetch all reviewer feedback, CI results, and code changes,
  then provide actionable analysis. With a PR number, reviews that single PR. Without
  arguments, reviews ALL open PRs (excluding please-release). Use when the user says
  "review this PR", "check PR feedback", "what did reviewers say", "review all PRs",
  "address review comments", or any variation involving pull request review.
---

# PR Review

Collect every piece of feedback on one or all open pull requests, then deliver focused,
actionable reviews with enough context to learn from each finding.

## Step 1 — Determine which PRs to review

### Mode A: specific PR (argument provided)

If `$ARGUMENTS` contains a PR number, review that single PR. Skip to "Collect PR data"
below.

### Mode B: all open PRs (no argument)

If `$ARGUMENTS` is empty, list every open PR and filter:

```bash
gh pr list --state open --json number,title,author,headRefName \
  --jq '.[] | select((.author.login == "please-release" | not) and (.headRefName | startswith("please-release") | not))'
```

This excludes PRs authored by `please-release` AND PRs from branches starting with
`please-release` (covers both bot-authored PRs and release branches).

If the list is empty, say "No open PRs to review (excluding please-release)" and stop.

Otherwise, process each PR in sequence using the steps below. Produce a **separate full
review per PR**, each clearly headed with the PR number and title. After all individual
reviews, add a final **Cross-PR summary** (see Step 5).

## Step 2 — Collect PR data

For each PR being reviewed, run the bundled collection script. It hits all 5 GitHub API
endpoints that store review feedback (metadata, changed files, reviews, inline review
comments, conversation comments, and CI status) in a single deterministic pass with
pagination.

```bash
bash .github/skills/pr-review/fetch-pr-feedback.sh <PR_NUMBER>
```

**This step is mandatory for every PR.** Do not skip it. Do not substitute ad-hoc `gh`
calls. GitHub splits review feedback across 3 separate API resources and agents
routinely miss inline review comments — the most actionable kind — when they only query
one endpoint.

The script returns a single JSON object. Confirm you received all keys: `metadata`,
`changed_files`, `reviews`, `review_comments`, `conversation_comments`, `ci_status`. If
any key is missing or empty, say so explicitly — never silently skip a section.

## Step 3 — Read changed files

For every file listed in `changed_files`, read the full current file (not just the diff
hunks). You need surrounding context to judge patterns, architecture, and whether tests
cover the change.

## Step 4 — Analyze via parallel sub-agent fan-out

Pass the collected PR data (changed files, diffs, full file contents) to all 4
perspective reviewer sub-agents **in parallel**:

1. **security-reviewer** — injection surface, secrets, input validation
2. **maintainability-reviewer** — complexity, coupling, naming, conventions
3. **performance-reviewer** — allocations, N+1, blocking I/O, hot paths
4. **quality-reviewer** — correctness, edge cases, test coverage, idioms

Each returns JSON conforming to `review-findings.schema.json`.

Merge all findings into a unified list. Then convert GitHub reviewer comments (from
`reviews`, `review_comments`, `conversation_comments`) into the same findings format
with `source` set to the reviewer's GitHub login.

## Step 5 — Output: structured tabular format

### Per-PR review

**1. PR Summary** (2-3 lines max) — what the PR does, branch, author.

**2. CI Status Table**

| Check | Status | Details |
|-------|--------|---------|
| {name} | ✅/❌ | {detail} |

**3. Perspective Summaries** — 4 mini-cards:

> **{Perspective}**: {verdict: clean / N findings} — {key finding or "no issues"}

**4. Findings Table** (sorted CRITICAL → MAJOR → MINOR → INFO)

| # | Sev | Source | File:Line | Finding | Recommendation | Effort |
|---|-----|--------|-----------|---------|----------------|--------|

All findings from all sources (sub-agents + GitHub reviewers) merged and sorted.

**5. Deep Dive** (MAJOR and MINOR only) — expandable `<details>` blocks per finding:
- **What** the issue is
- **Why** the recommended approach is better
- **Which principle** applies (design pattern or SOLID principle)
- **One gotcha** — common pitfall related to the fix

Keep lightweight — teaching in context, not lectures.

**6. CI Improvement Hints**

| Finding | Tool | Where | How |
|---------|------|-------|-----|

Suggestions for catching findings automatically in CI/pre-commit.

**7. Implementation Options**

```
[A] Fix all findings (full sweep)
[B] Fix CRITICAL + MAJOR only, create beads for MINOR + INFO
[C] Fix CRITICAL + MAJOR + MINOR, defer INFO to follow-up
[D] Create beads for all findings — review only, no code changes
[E] Custom selection (user specifies which findings to fix)
```

### Cross-PR summary (multi-PR mode only)

- **Overview table** — PR number, title, author, verdict (ready / needs-work / blocked),
  count of blocking findings
- **Cross-cutting issues** — patterns appearing in more than one PR
- **Suggested review order** — which PRs to tackle first by dependency and severity

## Step 6 — Implementation flow

After user selects an implementation option:

1. **Fix findings**: invoke the **implementation-subagent** with the specific findings to fix
2. **Defer findings**: present beads task creation list, ask user to confirm
3. **Push**: push changes to remote
4. **Wait for CI**: `task ci:wait -- <pr-number>`
5. **Present CI results**
6. **Ask user**: "CI passed. Ready to merge?" — only merge on explicit confirmation, **never auto-merge**

### Tone

- Direct and specific. No filler.
- Acknowledge trade-offs when recommending changes.
- Reinforce good patterns — when something is well done, say so.
- Never silently omit a section. If no findings for a category, state that explicitly.
