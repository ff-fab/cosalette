#!/usr/bin/env bash
# Warn when an open / in_progress beads issue is already referenced by work that
# has been merged to main — i.e. work that shipped but was never closed.
#
# Why not `bd orphans`? bd 1.1.2 ships that command, but it answers a different
# question: it scans the CURRENT branch with a bounded lookback. So it (a) flags
# unmerged work-in-progress on your own branch as an orphan, and (b) misses
# references older than its window — verified: it does not find cos-5zf, which is
# open and named on main in commits 298f933 / 7e1f5b5 (2026-04-04). `bd doctor`
# also reports the check as "N/A (not yet implemented for Dolt backend)". This
# script asks the question that actually matters here: is it merged to main and
# still open?
#
# This is a WARNING, not a gate: it exits 0 even on findings. Set
# BEADS_CHECK_STRICT=1 to make findings fail instead.

set -uo pipefail

BASE_REF="${BEADS_CHECK_REF:-origin/main}"
IGNORE_FILE="${BEADS_CHECK_IGNORE:-.beads/check-ignore}"

command -v bd >/dev/null 2>&1 || {
    echo "beads:check: bd is not installed — skipping."
    exit 0
}
command -v jq >/dev/null 2>&1 || {
    echo "beads:check: jq is not installed — skipping."
    exit 0
}
git rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null || {
    echo "beads:check: ${BASE_REF} not found — run 'git fetch origin'. Skipping."
    exit 0
}

# Issue IDs deliberately allowed to stay open despite being named on main
# (e.g. backlog items mentioned in passing by the commit that created them).
ignored=" "
if [ -f "${IGNORE_FILE}" ]; then
    ignored=" $(sed 's/#.*//' "${IGNORE_FILE}" | tr 'A-Z' 'a-z' | tr -s '[:space:]' ' ') "
fi

hits=0
for id in $(
    {
        bd list --status open --json
        bd list --status in_progress --json
    } 2>/dev/null | jq -r '.[]?.id' | tr 'A-Z' 'a-z' | sort -u
); do
    case "${ignored}" in *" ${id} "*) continue ;; esac
    # Word-boundary match so cos-a8n does not match cos-a8n.4 (and vice versa).
    escaped=$(printf '%s' "${id}" | sed 's/\./\\./g')
    match=$(git log -1 --format='%h %ad %s' --date=short -E -i \
        --grep="(^|[^a-z0-9._-])${escaped}([^a-z0-9._-]|\$)" "${BASE_REF}" 2>/dev/null) || continue
    [ -n "${match}" ] || continue
    if [ "${hits}" -eq 0 ]; then
        echo "beads:check: open issues already referenced by work merged to ${BASE_REF}:"
    fi
    printf '  %s — %s\n' "${id}" "${match}"
    hits=$((hits + 1))
done

if [ "${hits}" -eq 0 ]; then
    echo "beads:check: ✓ no open issues referenced by merged work on ${BASE_REF}"
    exit 0
fi

echo "beads:check: ${hits} issue(s) above look shipped but are still open."
echo "  Close them once the PR has MERGED:  bd close <id> && task beads:push"
echo "  Intentionally staying open? add the id to ${IGNORE_FILE}"
[ "${BEADS_CHECK_STRICT:-0}" = "1" ] && exit 1
exit 0
