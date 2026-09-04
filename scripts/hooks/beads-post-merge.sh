#!/usr/bin/env bash
# Beads post-merge hook: pull upstream Dolt changes after a merge.
#
# The pull is unconditional. It used to be gated on
# `git diff-tree ORIG_HEAD HEAD -- .beads/`, which asked "did this merge change
# tracked files under .beads/?". That was meaningful only while issues.jsonl was
# committed; since the 2026-08 audit the only tracked .beads/ files are config.yaml,
# metadata.json, hooks/, README.md and .gitignore, which change a few times a year —
# so the gate made the hook effectively dead while four docs still described it as
# working. Issue data now moves over the Dolt remote, which that gate could never
# observe, so there is nothing left to test: just pull.
#
# NOTE: git runs no post-merge hook at all on `git pull --rebase` — the project's
# documented workflow — so this is a safety net, not the mechanism. Run
# `task beads:pull` yourself after pulling.
if [ ! -d .beads ] || ! command -v bd >/dev/null 2>&1; then
    exit 0
fi

# Non-blocking: a transient network/auth failure must not wedge a merge.
bd dolt pull || echo "WARNING - bd dolt pull failed; beads data may be stale. Run \"task beads:pull\" manually." >&2
exit 0
