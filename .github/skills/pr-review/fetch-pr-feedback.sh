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
# data. stdout carries only JSON: gh's stderr (errors, upgrade or "hint:" notices) passes
# through to stderr and is never mixed into the data. If any gh call fails, the script
# exits non-zero with gh's error on stderr instead of returning empty sections.

set -euo pipefail

# ---------- prerequisites ----------
for cmd in gh jq; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "Error: '$cmd' is required but not installed." >&2
        exit 1
    }
done

# ---------- resolve PR number ----------
PR_NUMBER="${1:-}"

if [[ -z "$PR_NUMBER" ]]; then
    PR_NUMBER=$(gh pr view --json number --jq '.number' 2>/dev/null || true)
    if [[ -z "$PR_NUMBER" ]]; then
        echo "Error: No PR number provided and no PR found for the current branch." >&2
        echo "Usage: bash fetch-pr-feedback.sh [PR_NUMBER]" >&2
        exit 1
    fi
fi

if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid PR number '${PR_NUMBER}' — must be a positive integer." >&2
    exit 1
fi

REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')

echo "Fetching all feedback for PR #${PR_NUMBER} in ${REPO}..." >&2

# ---------- helpers ----------
# gh api with stdout reserved for JSON. gh's stderr (e.g. "hint: ... gh event listen"
# notices) goes to a temp file so it can never corrupt the data; it is shown only when
# the call fails. No "[]" fallback: an empty section must mean "no feedback", never
# "fetch failed". Under `set -e` a failed assignment below aborts the script.
gh_api() {
    local err
    err=$(mktemp)
    if ! gh api "$@" 2>"$err"; then
        echo "Error: 'gh api $*' failed:" >&2
        cat "$err" >&2
        rm -f "$err"
        return 1
    fi
    rm -f "$err"
}

# Fetches all pages of a list endpoint and merges the page arrays into one.
fetch_all_pages() {
    gh_api --paginate "$1" | jq -s 'add // []'
}

# ---------- 1. PR metadata ----------
echo "  [1/6] PR metadata..." >&2
PR_META=$(gh_api "repos/${REPO}/pulls/${PR_NUMBER}" \
    --jq '{
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
    }')

# ---------- 2. Changed files ----------
echo "  [2/6] Changed files..." >&2
CHANGED_FILES=$(fetch_all_pages "repos/${REPO}/pulls/${PR_NUMBER}/files" | \
    jq '[.[] | {filename, status, additions, deletions, changes, patch: (.patch // "" | if length > 2000 then .[0:2000] + "\n... (truncated)" else . end)}]')

# ---------- 3. PR reviews (top-level approve/request-changes/comment) ----------
echo "  [3/6] PR reviews..." >&2
REVIEWS=$(fetch_all_pages "repos/${REPO}/pulls/${PR_NUMBER}/reviews" | \
    jq '[.[] | {id, author: .user.login, state, body, submitted_at}]')

# ---------- 4. Inline review comments (CRITICAL — line-level on the diff) ----------
echo "  [4/6] Inline review comments (line-level)..." >&2
REVIEW_COMMENTS=$(fetch_all_pages "repos/${REPO}/pulls/${PR_NUMBER}/comments" | \
    jq '[.[] | {
        id,
        author: .user.login,
        path,
        line: (.line // .original_line),
        side: (.side // "RIGHT"),
        diff_hunk,
        body,
        created_at,
        in_reply_to_id
    }]')

# ---------- 5. PR conversation comments (issue-level, not attached to code) ----------
echo "  [5/6] Conversation comments..." >&2
CONVERSATION_COMMENTS=$(fetch_all_pages "repos/${REPO}/issues/${PR_NUMBER}/comments" | \
    jq '[.[] | {id, author: .user.login, body, created_at}]')

# ---------- 6. CI status checks ----------
echo "  [6/6] CI status checks..." >&2
# Combine both commit statuses and check runs for full coverage.
HEAD_SHA=$(gh_api "repos/${REPO}/pulls/${PR_NUMBER}" --jq '.head.sha')

# Commit status endpoint returns {state, statuses: [...]}
STATUSES=$(gh_api "repos/${REPO}/commits/${HEAD_SHA}/status" \
    --jq '{state, statuses: [.statuses[] | {context, state, description, target_url}]}')

# Check-runs endpoint returns {total_count, check_runs: [...]}, NOT a bare
# array — so we must NOT use fetch_all_pages (which assumes arrays).
# Use ?per_page=100 as a query param (NOT -F, which forces POST).
CHECK_RUNS=$(gh_api "repos/${REPO}/commits/${HEAD_SHA}/check-runs?per_page=100" \
    --jq '[.check_runs[]? | {name, status, conclusion, html_url, output: {title: .output.title, summary: (.output.summary // "" | if length > 500 then .[0:500] + "... (truncated)" else . end)}}]')

STATUS_STATE=$(echo "$STATUSES" | jq -r '.state')
STATUS_ARRAY=$(echo "$STATUSES" | jq '.statuses')

# Compute aggregate state from BOTH legacy statuses and check runs.
# Priority: failure > pending > success > unknown.
# Allowlist for success: only success/neutral/skipped count as non-failure;
# any other completed conclusion (timed_out, cancelled, startup_failure, stale, etc.)
# is treated as failure so the aggregate never fails open.
STATE=$(jq -nr \
    --arg status_state "$STATUS_STATE" \
    --argjson statuses "$STATUS_ARRAY" \
    --argjson check_runs "$CHECK_RUNS" \
    '
    def is_success_conclusion: . == "success" or . == "neutral" or . == "skipped";
    def has_failure:
        ($statuses | any(.state == "failure" or .state == "error")) or
        ($check_runs | any(.status == "completed" and (.conclusion | is_success_conclusion | not)));
    def has_pending:
        ($status_state == "pending" and ($statuses | length) > 0) or
        ($check_runs | any(.status != "completed"));
    if has_failure then "failure"
    elif has_pending then "pending"
    elif (($statuses | length) + ($check_runs | length)) == 0 then "unknown"
    else "success"
    end
    ')

CI_STATUS=$(jq -n \
    --arg state "$STATE" \
    --argjson statuses "$STATUS_ARRAY" \
    --argjson check_runs "$CHECK_RUNS" \
    '{state: $state, statuses: $statuses, check_runs: $check_runs}')

# ---------- assemble final output ----------
REVIEW_COMMENT_COUNT=$(echo "$REVIEW_COMMENTS" | jq 'length')
CONVERSATION_COUNT=$(echo "$CONVERSATION_COMMENTS" | jq 'length')
REVIEW_COUNT=$(echo "$REVIEWS" | jq 'length')
FILE_COUNT=$(echo "$CHANGED_FILES" | jq 'length')

echo "" >&2
echo "Summary: ${FILE_COUNT} files changed, ${REVIEW_COUNT} reviews, ${REVIEW_COMMENT_COUNT} inline comments, ${CONVERSATION_COUNT} conversation comments" >&2

jq -n \
    --argjson metadata "$PR_META" \
    --argjson changed_files "$CHANGED_FILES" \
    --argjson reviews "$REVIEWS" \
    --argjson review_comments "$REVIEW_COMMENTS" \
    --argjson conversation_comments "$CONVERSATION_COMMENTS" \
    --argjson ci_status "$CI_STATUS" \
    '{
        metadata: $metadata,
        changed_files: $changed_files,
        reviews: $reviews,
        review_comments: $review_comments,
        conversation_comments: $conversation_comments,
        ci_status: $ci_status
    }'
