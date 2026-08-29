#!/usr/bin/env bash
# Beads post-merge hook: pull upstream Dolt changes after a non-fast-forward merge.
# NOTE: does not fire on `git pull --rebase` — run `task beads:pull` manually in that workflow.
if [ ! -d .beads ] || ! command -v bd >/dev/null 2>&1; then
    exit 0
fi

CHANGED=$(git diff-tree -r --name-only ORIG_HEAD HEAD -- ".beads/" 2>/dev/null)
if [ -n "$CHANGED" ]; then
    echo "Beads data updated by merge, pulling..."
    bd dolt pull || echo "WARNING - bd dolt pull failed; beads data may be stale. Run \"task beads:pull\" manually." >&2
fi
