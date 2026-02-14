#!/bin/bash
# Render phase progress bars from beads epic data.
# Called by: task plan
#
# Shows all epics (phases) with color-coded progress bars:
#   ● green  = completed (100%)
#   ○ yellow = in progress (>0%)
#   ○ dim    = not started (0%)

set -euo pipefail

BAR_WIDTH=20

# Get open epic progress (has total/closed children counts)
EPIC_STATUS=$(bd epic status --json 2>/dev/null)

# Get ALL epics (open + closed), sorted alphabetically by title (Phase 0, 1, 2, ...)
ALL_EPICS=$(bd list --type epic --all --json 2>/dev/null | jq -r '
    sort_by(.title) | .[] | [.id, .status, .title] | @tsv')

if [ -z "$ALL_EPICS" ]; then
    echo "No epics found." >&2
    exit 1
fi

# Calculate max title width for aligned columns
MAX_TITLE=0
while IFS=$'\t' read -r _ _ title; do
    len=${#title}
    (( len > MAX_TITLE )) && MAX_TITLE=$len
done <<< "$ALL_EPICS"

echo ""
while IFS=$'\t' read -r id status title; do
    if [ "$status" = "closed" ]; then
        # Closed epic: count children via parent filter (all closed by definition)
        total=$(bd list --parent "$id" --all --json --limit 0 2>/dev/null | jq 'length')
        closed=$total
    else
        # Open epic: use pre-fetched epic status data
        total=$(echo "$EPIC_STATUS" | jq -r --arg id "$id" \
            '.[] | select(.epic.id == $id) | .total_children')
        closed=$(echo "$EPIC_STATUS" | jq -r --arg id "$id" \
            '.[] | select(.epic.id == $id) | .closed_children')
    fi

    # Calculate bar segments and percentage
    if [ "$total" -gt 0 ] 2>/dev/null; then
        filled=$(( closed * BAR_WIDTH / total ))
        empty=$(( BAR_WIDTH - filled ))
        pct=$(( closed * 100 / total ))
    else
        filled=0; empty=$BAR_WIDTH; pct=0
    fi

    # Build bar characters
    bar_fill=""; bar_empty=""
    for ((i=0; i<filled; i++)); do bar_fill+="█"; done
    for ((i=0; i<empty; i++)); do bar_empty+="░"; done

    # Color scheme: green=done, yellow=in-progress, dim=not started
    if [ "$pct" -eq 100 ]; then
        bullet="\033[32m●\033[0m"
        bar="\033[32m${bar_fill}\033[0m"
    elif [ "$pct" -gt 0 ]; then
        bullet="\033[33m○\033[0m"
        bar="\033[33m${bar_fill}\033[2m${bar_empty}\033[0m"
    else
        bullet="\033[2m○\033[0m"
        bar="\033[2m${bar_empty}\033[0m"
    fi

    printf " %b  %-8s  %-${MAX_TITLE}s  %b  %3d%%  (%d/%d)\n" \
        "$bullet" "$id" "$title" "$bar" "$pct" "$closed" "$total"

done <<< "$ALL_EPICS"
echo ""
