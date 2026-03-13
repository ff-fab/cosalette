---
name: pr-review
description:
  Review open pull requests — fetch all reviewer feedback, CI results, and code changes,
  then provide actionable analysis. With a PR number, reviews that single PR. Without
  arguments, reviews ALL open PRs (excluding please-release). Use when the user says
  "review this PR", "check PR feedback", "what did reviewers say", "review all PRs",
  "address review comments", or any variation involving pull request review.
allowed-tools:
  - Bash(gh *)
  - Bash(bash *)
  - Read
  - Grep
---

<!-- ultrathink -->

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
bash "$(skill_dir)/fetch-pr-feedback.sh" <PR_NUMBER>
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

## Step 4 — Analyze

Work through these in order. Be concrete — reference exact files and line numbers.
Propose actual fixes, not vague suggestions.

### CI & status checks

Identify failing checks and their root cause. Flag coverage regressions with the
specific modules affected. Note flaky-test patterns if visible.

### Review comments

Triage ALL comments from all three sources (`reviews`, `review_comments`,
`conversation_comments`) into:

- **Blocking** — must resolve before merge
- **Suggestion** — optional improvement
- **Question** — needs a reply or clarification

For each, propose a concrete fix or response.

Pay special attention to `review_comments` (inline findings on specific diff lines).
Group them by file.

### Code quality

Review the diff for:

- **Correctness** — logic errors, edge cases, missing error handling
- **Consistency** — adherence to the project's existing conventions (check
  `.github/instructions/`, linter configs, existing patterns)
- **Performance** — unnecessary allocations, N+1 queries, blocking I/O
- **Security** — input validation, secrets exposure, injection surface
- **Test coverage** — missing or insufficient tests for new behavior

### Language idioms

Spot opportunities to use idiomatic constructs even in correct code. Mention when an
idiom helps and when it would hurt readability. Reference PEPs, RFCs, or official docs
by number where applicable.

## Step 5 — Teach alongside findings

For significant findings (not every nitpick), weave in brief educational context:

1. **What** the pattern or issue is
2. **Why** the recommended approach is better
3. **Which principle** applies — name the design pattern (Strategy, Factory, Observer …)
   or SOLID principle (SRP, OCP …) if one fits naturally
4. **One gotcha** — a common pitfall related to the fix

Keep this lightweight. A sentence or two per point, integrated into the finding — not a
separate lecture section. If the code is already good, say so and briefly explain why it
works well.

## Step 6 — Output

### Per-PR review

Structure each PR review clearly. Use this as a guide, not a rigid template — adapt
section depth to what the PR actually warrants:

1. **PR summary** — one-paragraph description of what the PR does
2. **Data collected** — counts of files changed, reviews, inline comments, conversation
   comments (confirms nothing was missed)
3. **CI status** — pass/fail per check, failure details if any
4. **Review comment triage** — blocking → suggestions → questions, each with proposed
   fix or response
5. **Code quality findings** — grouped by category, each with file:line, finding, fix,
   and teaching note where warranted
6. **Recommended actions** — prioritized list; note which changes can be batched
   together vs. need sequential work

### Cross-PR summary (multi-PR mode only)

When reviewing multiple PRs, end with a summary section:

- **Overview table** — PR number, title, author, verdict (ready / needs-work / blocked),
  count of blocking findings
- **Cross-cutting issues** — patterns that appear in more than one PR (e.g. same linting
  violation, repeated missing test coverage, shared security concern)
- **Suggested review order** — which PRs to tackle first, considering dependencies
  between them and severity of findings

### Tone

- Be direct and specific. No filler.
- Acknowledge trade-offs when recommending changes.
- When something is already well done, say so — reinforce good patterns.
- Never silently omit a section. If there are no findings for a category, state that
  explicitly.
