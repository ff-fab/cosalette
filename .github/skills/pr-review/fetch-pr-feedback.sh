#!/usr/bin/env bash
# fetch-pr-feedback.sh — Deterministically fetch ALL review feedback for a PR.
#
# Usage:
#     bash fetch-pr-feedback.sh [PR_NUMBER]
#
# If PR_NUMBER is omitted, auto-detects the PR for the current branch.
#
# Outputs a single JSON object to stdout with keys:
#     metadata, reviews, review_comments, conversation_comments, changed_files, ci_status
#
# Each section key is always present; list sections are empty arrays when they have no
# data. stdout carries only JSON: gh's stderr (errors, upgrade or "hint:" notices) is
# kept out of it and shown only when a call fails. If any gh call fails, the script
# exits non-zero with gh's error on stderr instead of returning empty sections.

set -euo pipefail

# ---------- prerequisites ----------
for cmd in gh jq; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "Error: '$cmd' is required but not installed." >&2
        exit 1
    }
done

# Sections are passed to jq as files: one argv string is capped at 128 KiB on Linux,
# which a large PR's JSON exceeds.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Run gh with stdout reserved for JSON; on failure print gh's stderr and return 1.
# Callers never fall back to "[]": an empty section must mean "no feedback", never
# "fetch failed". Under `set -e` a failed call below aborts the script.
gh_json() {
    if ! gh "$@" 2>"$WORK/stderr"; then
        echo "Error: 'gh $*' failed:" >&2
        cat "$WORK/stderr" >&2
        return 1
    fi
}

# ---------- resolve PR number ----------
PR_NUMBER="${1:-}"

if [[ -z "$PR_NUMBER" ]]; then
    PR_NUMBER=$(gh_json pr view --json number --jq '.number') || {
        echo "Error: No PR number provided and no PR found for the current branch." >&2
        echo "Usage: bash fetch-pr-feedback.sh [PR_NUMBER]" >&2
        exit 1
    }
fi

if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid PR number '${PR_NUMBER}' — must be a positive integer." >&2
    exit 1
fi

REPO=$(gh_json repo view --json nameWithOwner --jq '.nameWithOwner')
PR="repos/${REPO}/pulls/${PR_NUMBER}"

echo "Fetching all feedback for PR #${PR_NUMBER} in ${REPO}..." >&2

# Fetches all pages of a list endpoint into $WORK/<name>.json, merged into one array
# and projected through the jq filter.
fetch_all_pages() {
    local name="$1" endpoint="$2" filter="$3"
    gh_json api --paginate "$endpoint" >"$WORK/$name.raw"
    jq -s "add // [] | $filter" "$WORK/$name.raw" >"$WORK/$name.json"
}

# ---------- 1. PR metadata ----------
echo "  [1/6] PR metadata..." >&2
gh_json api "$PR" >"$WORK/pr.raw"
jq '{
    number,
    title,
    body,
    state,
    draft,
    mergeable_state: .mergeable_state,
    labels: [.labels[].name],
    author: .user.login,
    created_at,
    updated_at,
    html_url
}' "$WORK/pr.raw" >"$WORK/metadata.json"
HEAD_SHA=$(jq -r '.head.sha' "$WORK/pr.raw")

# ---------- 2. Changed files ----------
echo "  [2/6] Changed files..." >&2
fetch_all_pages changed_files "$PR/files" \
    '[.[] | {filename, status, additions, deletions, changes, patch: (.patch // "" | if length > 2000 then .[0:2000] + "\n... (truncated)" else . end)}]'

# ---------- 3. PR reviews (top-level approve/request-changes/comment) ----------
echo "  [3/6] PR reviews..." >&2
fetch_all_pages reviews "$PR/reviews" \
    '[.[] | {id, author: .user.login, state, body, submitted_at}]'

# ---------- 4. Inline review comments (CRITICAL — line-level on the diff) ----------
echo "  [4/6] Inline review comments (line-level)..." >&2
fetch_all_pages review_comments "$PR/comments" \
    '[.[] | {
        id,
        author: .user.login,
        path,
        line: (.line // .original_line),
        side: (.side // "RIGHT"),
        diff_hunk,
        body,
        created_at,
        in_reply_to_id
    }]'

# ---------- 5. PR conversation comments (issue-level, not attached to code) ----------
echo "  [5/6] Conversation comments..." >&2
fetch_all_pages conversation_comments "repos/${REPO}/issues/${PR_NUMBER}/comments" \
    '[.[] | {id, author: .user.login, body, created_at}]'

# ---------- 6. CI status checks ----------
echo "  [6/6] CI status checks..." >&2
# Combine both commit statuses and check runs for full coverage.
# Commit status endpoint returns {state, statuses: [...]}
gh_json api "repos/${REPO}/commits/${HEAD_SHA}/status" \
    --jq '{state, statuses: [.statuses[] | {context, state, description, target_url}]}' \
    >"$WORK/statuses.json"

# Check-runs endpoint returns {total_count, check_runs: [...]}, NOT a bare
# array — so we must NOT use fetch_all_pages (which assumes arrays).
# Use ?per_page=100 as a query param (NOT -F, which forces POST).
gh_json api "repos/${REPO}/commits/${HEAD_SHA}/check-runs?per_page=100" \
    --jq '[.check_runs[]? | {name, status, conclusion, html_url, output: {title: .output.title, summary: (.output.summary // "" | if length > 500 then .[0:500] + "... (truncated)" else . end)}}]' \
    >"$WORK/check_runs.json"

# Aggregate state from BOTH legacy statuses and check runs.
# Priority: failure > pending > success > unknown.
# Allowlist for success: only success/neutral/skipped count as non-failure;
# any other completed conclusion (timed_out, cancelled, startup_failure, stale, etc.)
# is treated as failure so the aggregate never fails open.
jq -n \
    --slurpfile s "$WORK/statuses.json" \
    --slurpfile c "$WORK/check_runs.json" \
    '
    $s[0].state as $status_state | $s[0].statuses as $statuses | $c[0] as $check_runs |
    def is_success_conclusion: . == "success" or . == "neutral" or . == "skipped";
    def has_failure:
        ($statuses | any(.state == "failure" or .state == "error")) or
        ($check_runs | any(.status == "completed" and (.conclusion | is_success_conclusion | not)));
    def has_pending:
        ($status_state == "pending" and ($statuses | length) > 0) or
        ($check_runs | any(.status != "completed"));
    {
        state: (
            if has_failure then "failure"
            elif has_pending then "pending"
            elif (($statuses | length) + ($check_runs | length)) == 0 then "unknown"
            else "success"
            end
        ),
        statuses: $statuses,
        check_runs: $check_runs
    }
    ' >"$WORK/ci_status.json"

# ---------- assemble final output ----------
count() { jq 'length' "$WORK/$1.json"; }
echo "" >&2
echo "Summary: $(count changed_files) files changed, $(count reviews) reviews, $(count review_comments) inline comments, $(count conversation_comments) conversation comments" >&2

jq -n \
    --slurpfile metadata "$WORK/metadata.json" \
    --slurpfile changed_files "$WORK/changed_files.json" \
    --slurpfile reviews "$WORK/reviews.json" \
    --slurpfile review_comments "$WORK/review_comments.json" \
    --slurpfile conversation_comments "$WORK/conversation_comments.json" \
    --slurpfile ci_status "$WORK/ci_status.json" \
    '{
        metadata: $metadata[0],
        changed_files: $changed_files[0],
        reviews: $reviews[0],
        review_comments: $review_comments[0],
        conversation_comments: $conversation_comments[0],
        ci_status: $ci_status[0]
    }'
