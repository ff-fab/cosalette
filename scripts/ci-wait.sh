#!/bin/bash
# Wait for all CI checks on a PR to complete, then report results.
# Called by: task ci:wait -- <pr-number>
#
# Polls `gh pr checks` every INTERVAL seconds until no checks are
# IN_PROGRESS / PENDING / QUEUED. Then prints a summary table with
# pass/fail status and links to failed runs for easy debugging.
#
# Exit codes:
#   0 — all checks passed (or skipped)
#   1 — one or more checks failed
#   2 — usage error (missing PR number or gh not available)

set -euo pipefail

INTERVAL="${CI_WAIT_INTERVAL:-15}"

# ── Argument handling ────────────────────────────────────────────────

PR="${1:-}"
if [ -z "$PR" ]; then
    # Auto-detect: use the PR associated with the current branch
    PR=$(gh pr view --json number --jq '.number' 2>/dev/null || true)
    if [ -z "$PR" ]; then
        echo "Usage: ci-wait.sh <pr-number>" >&2
        echo "   or: run from a branch with an open PR (auto-detect)" >&2
        exit 2
    fi
    echo "Auto-detected PR #${PR}"
fi

if ! command -v gh &>/dev/null; then
    echo "Error: gh CLI not found on PATH" >&2
    exit 2
fi

if ! command -v jq &>/dev/null; then
    echo "Error: jq not found on PATH" >&2
    exit 2
fi

# ── Poll loop ────────────────────────────────────────────────────────

echo "Waiting for CI on PR #${PR} (polling every ${INTERVAL}s)..."
echo ""

while true; do
    checks=$(gh pr checks "$PR" --json name,state,link 2>&1 | cat)

    # Guard against transient API errors (empty or non-JSON response)
    if ! echo "$checks" | jq empty 2>/dev/null; then
        echo "$(date +%H:%M:%S) — API returned non-JSON, retrying..."
        sleep "$INTERVAL"
        continue
    fi

    pending=$(echo "$checks" | jq '[.[] | select(.state == "IN_PROGRESS" or .state == "PENDING" or .state == "QUEUED")] | length')

    if [ "$pending" -eq 0 ]; then
        break
    fi

    echo "$(date +%H:%M:%S) — ${pending} check(s) still running..."
    sleep "$INTERVAL"
done

# ── Results ──────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  CI Results for PR #${PR}"
echo "════════════════════════════════════════════════════════════════"

failed=0
while IFS=$'\t' read -r name state link; do
    case "$state" in
        SUCCESS)  icon="✅" ;;
        SKIPPED)  icon="⬜" ;;
        FAILURE)  icon="❌"; failed=$((failed + 1)) ;;
        *)        icon="⚠️ "; failed=$((failed + 1)) ;;
    esac
    printf "  %s  %-40s %s\n" "$icon" "$name" "$state"
done < <(echo "$checks" | jq -r '.[] | [.name, .state, .link] | @tsv')

echo "════════════════════════════════════════════════════════════════"

if [ "$failed" -gt 0 ]; then
    echo ""
    echo "Failed checks (${failed}):"
    echo ""
    while IFS=$'\t' read -r name state link; do
        if [ "$state" != "SUCCESS" ] && [ "$state" != "SKIPPED" ]; then
            echo "  ❌ ${name}"
            echo "     ${link}"
            echo ""
        fi
    done < <(echo "$checks" | jq -r '.[] | [.name, .state, .link] | @tsv')
    echo "Tip: open the link(s) above to see full logs."
    exit 1
fi

echo ""
echo "All checks passed ✓"
exit 0
