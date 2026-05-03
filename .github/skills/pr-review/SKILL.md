---
name: pr-review
user-invocable: false
description: Review open pull requests — fetch all reviewer feedback, CI results, and code changes, then provide actionable analysis. With a PR number, reviews that single PR. Without arguments, reviews ALL open PRs (excluding release-please).
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
task pr:list
```

This runs `gh pr list` with the release-please exclusion filter built in.

If the list is empty, say "No open PRs to review (excluding release-please)" and stop.

Otherwise, process each PR in sequence using the steps below. Produce a **separate full
review per PR**, each clearly headed with the PR number and title. After all individual
reviews, add a final **Cross-PR summary** (see Step 5).

## Step 2 — Collect PR data

For each PR being reviewed, run the bundled collection script. It hits all 5 GitHub API
endpoints that store review feedback (metadata, changed files, reviews, inline review
comments, conversation comments, and CI status) in a single deterministic pass with
pagination.

```bash
task pr:feedback -- <PR_NUMBER>   # explicit PR number
task pr:feedback                  # auto-detects the PR for the current branch
```

**Important:** Use the JSON output directly — do **NOT** pipe through `jq` or transform
it. The script already curates and flattens the data into a compact schema. Piping
through `jq` risks schema mismatches and wasted API calls.

The script returns a single JSON object whose exact structure is defined in
`.github/skills/pr-review/feedback-schema.json`. Read that file for the full schema.

Key points about the schema:

- All `author` fields are plain strings (GitHub login), **not** objects.
- `ci_status.state` is an aggregate computed from both legacy commit statuses and modern
  check runs.

Confirm you received all keys: `metadata`, `changed_files`, `reviews`,
`review_comments`, `conversation_comments`, `ci_status`. If any key is missing or empty,
say so explicitly — never silently skip a section.

## Step 3 — Read changed files

For every file listed in `changed_files`, read full current file (not just diff hunks).
You need surrounding context to judge patterns, architecture, whether tests cover
change.

## Step 4 — Analyze via parallel sub-agent fan-out

Pass the collected PR data to all 4 perspective reviewer sub-agents **in parallel**:

1. **security-reviewer** — injection surface, secrets, input validation
2. **maintainability-reviewer** — complexity, coupling, naming, conventions
3. **performance-reviewer** — allocations, N+1, blocking I/O, hot paths
4. **quality-reviewer** — correctness, edge cases, test coverage, idioms

Each returns JSON conforming to `.github/agents/schemas/reviewer-output.schema.json`.

Merge all findings into unified list. Then convert GitHub reviewer comments (from
`reviews`, `review_comments`, `conversation_comments`) into same findings format
with `source` set to the reviewer's GitHub login.

## Step 5 — Teach alongside findings

For security findings, weave in brief educational context:

1. **What** the pattern or issue is
2. **Why** the recommended approach is better
3. **Which principle** applies
4. **One gotcha** — a common pitfall related to the fix

## Step 6 — Output: structured tabular format


### Per-PR review

**1. PR Summary** (2-3 lines max) — what the PR does, branch, author.

**2. CI Status Table**

| Check | Status | Details |
|-------|--------|---------|
| {name} | ✅/❌/⬜ | {detail} |

Use ✅ for passed, ❌ for failed, ⬜ for skipped/neutral.

For checks that include a `target_url` (particularly **Deploy Preview**), render the
Details value as a clickable markdown link: `[Preview](url)` or `[Details](url)`.

**3. Perspective Summaries** — 4 mini-cards:

> **{Perspective}**: {verdict: clean / N findings} — {key finding or "no issues"}

**4. Findings Table** (sorted CRITICAL → MAJOR → MINOR → INFO)

| # | Sev | Source | File:Line | Finding | Recommendation | Effort |
|---|-----|--------|-----------|---------|----------------|--------|

All findings from all sources (sub-agents + GitHub reviewers) merged and sorted.

**5. Implementation Options**

Present options as interactive quick-pick buttons so the user can select with one
click. Include your recommendation. The options are:

> **[A]** Fix all findings (full sweep) — implement everything in current PR
> **[B]** Fix CRITICAL + MAJOR only, create beads for MINOR + INFO
> **[C]** Fix CRITICAL + MAJOR + MINOR, ignore INFO
> **[E]** Custom selection (user specifies which findings to fix)

### Cross-PR summary (multi-PR mode only)

- **Overview table** — PR number, title, author, verdict (ready / needs-work / blocked),
  count of blocking findings
- **Cross-cutting issues** — patterns appearing in more than one PR
- **Suggested review order** — which PRs to tackle first by dependency and severity

## Step 7 — Implementation flow

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
