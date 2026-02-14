#!/usr/bin/env bash
# Format bd children output for the fzf preview pane in plan-interactive.sh.
#
# Transforms:
#   - Strips issue type tags ([task], [bug], etc.) — redundant in epic context
#   - Strips "blocks: ..." info — irrelevant in preview context
#   - Strips parent-only "blocked by" — bd shows (blocked by: <parent>) for all
#     children, which is the parent-child link, not a real blocker
#   - Dims genuinely blocked tasks (ANSI faint) for visual de-emphasis

set -euo pipefail

if [ "$#" -lt 1 ] || [ -z "${1:-}" ]; then
    echo "Usage: $0 <epic-id>" >&2
    exit 1
fi

EPIC_ID="$1"

# --- Handle virtual "Orphaned tasks" entry -------------------------------- #
if [ "$EPIC_ID" = "_orphan" ]; then
    ORPHAN_CACHE="${2:-}"

    # Use cached orphan IDs from plan-interactive.sh if available
    if [ -n "$ORPHAN_CACHE" ] && [ -s "$ORPHAN_CACHE" ]; then
        ORPHANS=$(cat "$ORPHAN_CACHE")
    else
        # Fallback: re-detect orphans (standalone invocation or stale cache)
        ALL_TASK_IDS=$(bd list --all --json --limit 0 2>/dev/null \
            | jq -r '.[] | select(.issue_type != "epic") | .id' | sort)
        EPIC_IDS=$(bd list --type epic --all --json --limit 0 2>/dev/null | jq -r '.[].id')
        PARENTED_IDS=""
        for eid in $EPIC_IDS; do
            children=$(bd list --parent "$eid" --all --json --limit 0 2>/dev/null | jq -r '.[].id')
            [ -n "$children" ] && PARENTED_IDS="${PARENTED_IDS:+$PARENTED_IDS
}$children"
        done
        PARENTED_IDS=$(echo "$PARENTED_IDS" | sort -u)
        if [ -n "$PARENTED_IDS" ]; then
            ORPHANS=$(comm -23 <(echo "$ALL_TASK_IDS") <(echo "$PARENTED_IDS"))
        else
            ORPHANS="$ALL_TASK_IDS"
        fi
    fi

    if [ -z "$ORPHANS" ]; then
        echo "No orphaned tasks."
        exit 0
    fi

    printf "\033[31m⚠ Tasks not parented to any epic\033[0m\n\n"

    # Bulk-fetch all tasks in one bd call, filter to orphan IDs (avoids N+1)
    orphan_ids_json=$(echo "$ORPHANS" | jq -R -s 'split("\n") | map(select(. != ""))')
    bd list --all --json --limit 0 2>/dev/null \
        | jq -r --argjson ids "$orphan_ids_json" '
                ($ids | map({key: ., value: true}) | from_entries) as $idset
                | [.[] | select(.issue_type != "epic") | select(.id as $i | $idset[$i])]
                | sort_by(.priority, .title) | .[]
                | "\(.status)\t\(.priority // 2)\t\(.id)\t\(.title)"' \
        | while IFS=$'\t' read -r status priority oid title; do
                case "$status" in
                    closed)      icon="✓" ;;
                    in_progress) icon="●" ;;
                    *)           icon="○" ;;
                esac
                printf "%s P%s  %s  %s\n" "$icon" "$priority" "$oid" "$title"
            done
    exit 0
fi

bd children "$EPIC_ID" 2>/dev/null \
    | sed \
            -e 's/\[[a-z]*\] - //' \
            -e 's/, blocks: [^)]*//' \
            -e 's/ (blocks: [^)]*)//' \
            -e "s/ (blocked by: ${EPIC_ID})//" \
            -e "s/blocked by: ${EPIC_ID}, \([^.]\)/blocked by: \1/" \
            -e "s/blocked by: ${EPIC_ID})/blocked by:)/" \
            -e 's/ (blocked by:)//' \
            -e "s/^○ ${EPIC_ID}\./○ \./" \
            -e 's/blocked by: /← /g' \
    | sed -e ':a' -e 's/\(← [^)]*\)lh-/\1/g' -e 'ta' \
    | awk '/←/{printf "\033[2m%s\033[0m\n",$0; next} {print}'
