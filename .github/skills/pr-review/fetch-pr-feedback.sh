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
# Each section key is always present. Sections that represent lists contain an empty array
# when they have no data, and also fall back to an empty array if fetching fails. The
# metadata section may contain {"error": "..."} on failure. Errors are always reported
# on stderr.

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

REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')

echo "Fetching all feedback for PR #${PR_NUMBER} in ${REPO}..." >&2

# ---------- helper: paginated gh api call ----------
# Fetches all pages and merges the JSON arrays into one.
# Uses jq -s to safely merge potentially separate page arrays into one.
fetch_all_pages() {
    local endpoint="$1"
    local raw
    raw=$(gh api --paginate "$endpoint" 2>/dev/null) || raw=""
    if [[ -z "$raw" ]]; then
        echo "[]"
    else
        echo "$raw" | jq -s 'add // []' 2>/dev/null || echo "[]"
    fi
}

# ---------- 1. PR metadata ----------
echo "  [1/6] PR metadata..." >&2
PR_META=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}" \
    --jq '{
        number,
        title,
        body,
        state,
        draft,
        mergeable_state: .mergeable_state,
        labels: [.labels[].name],
        user: .user.login,
        created_at,
        updated_at,
        html_url
    }' 2>/dev/null || echo '{"error": "Failed to fetch PR metadata"}')

# ---------- 2. Changed files ----------
echo "  [2/6] Changed files..." >&2
CHANGED_FILES=$(fetch_all_pages "repos/${REPO}/pulls/${PR_NUMBER}/files" | \
    jq '[.[] | {filename, status, additions, deletions, changes, patch: (.patch // "" | if length > 2000 then .[0:2000] + "\n... (truncated)" else . end)}]' \
    2>/dev/null || echo '[]')

# ---------- 3. PR reviews (top-level approve/request-changes/comment) ----------
echo "  [3/6] PR reviews..." >&2
REVIEWS=$(fetch_all_pages "repos/${REPO}/pulls/${PR_NUMBER}/reviews" | \
    jq '[.[] | {id, user: .user.login, state, body, submitted_at}]' \
    2>/dev/null || echo '[]')

# ---------- 4. Inline review comments (CRITICAL — line-level on the diff) ----------
echo "  [4/6] Inline review comments (line-level)..." >&2
REVIEW_COMMENTS=$(fetch_all_pages "repos/${REPO}/pulls/${PR_NUMBER}/comments" | \
    jq '[.[] | {
        id,
        user: .user.login,
        path,
        line: (.line // .original_line),
        side: (.side // "RIGHT"),
        diff_hunk,
        body,
        created_at,
        in_reply_to_id
    }]' 2>/dev/null || echo '[]')

# ---------- 5. PR conversation comments (issue-level, not attached to code) ----------
echo "  [5/6] Conversation comments..." >&2
CONVERSATION_COMMENTS=$(fetch_all_pages "repos/${REPO}/issues/${PR_NUMBER}/comments" | \
    jq '[.[] | {id, user: .user.login, body, created_at}]' \
    2>/dev/null || echo '[]')

# ---------- 6. CI status checks ----------
echo "  [6/6] CI status checks..." >&2
# Combine both commit statuses and check runs for full coverage.
HEAD_SHA=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}" --jq '.head.sha' 2>/dev/null || echo "")

CI_STATUS='{"state": "unknown", "statuses": [], "check_runs": []}'
if [[ -n "$HEAD_SHA" ]]; then
    # Commit status endpoint returns {state, statuses: [...]}
    STATUSES=$(gh api "repos/${REPO}/commits/${HEAD_SHA}/status" \
        --jq '{state, statuses: [.statuses[] | {context, state, description, target_url}]}' \
        2>/dev/null || echo '{"state": "unknown", "statuses": []}')

    # Check-runs endpoint returns {total_count, check_runs: [...]}, NOT a bare
    # array — so we must NOT use fetch_all_pages (which assumes arrays).
    # Use ?per_page=100 as a query param (NOT -F, which forces POST).
    CHECK_RUNS=$(gh api "repos/${REPO}/commits/${HEAD_SHA}/check-runs?per_page=100" \
        --jq '[.check_runs[]? | {name, status, conclusion, html_url, output: {title: .output.title, summary: (.output.summary // "" | if length > 500 then .[0:500] + "... (truncated)" else . end)}}]' \
        2>/dev/null || echo '[]')

    STATE=$(echo "$STATUSES" | jq -r '.state' 2>/dev/null || echo 'unknown')
    STATUS_ARRAY=$(echo "$STATUSES" | jq '.statuses' 2>/dev/null || echo '[]')

    CI_STATUS=$(jq -n \
        --arg state "$STATE" \
        --argjson statuses "$STATUS_ARRAY" \
        --argjson check_runs "$CHECK_RUNS" \
        '{state: $state, statuses: $statuses, check_runs: $check_runs}' \
        2>/dev/null || echo '{"state": "unknown", "statuses": [], "check_runs": []}')
fi

# ---------- assemble final output ----------
REVIEW_COMMENT_COUNT=$(echo "$REVIEW_COMMENTS" | jq 'length' 2>/dev/null || echo '0')
CONVERSATION_COUNT=$(echo "$CONVERSATION_COMMENTS" | jq 'length' 2>/dev/null || echo '0')
REVIEW_COUNT=$(echo "$REVIEWS" | jq 'length' 2>/dev/null || echo '0')
FILE_COUNT=$(echo "$CHANGED_FILES" | jq 'length' 2>/dev/null || echo '0')

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
