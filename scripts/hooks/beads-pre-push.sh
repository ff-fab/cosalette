#!/usr/bin/env bash
# Beads pre-push hook: check tracked .beads/ changes, then replicate DB to Dolt remote.
if [ ! -d .beads ] || ! command -v bd >/dev/null 2>&1; then
    exit 0
fi

# Fail if tracked .beads/ files (config.yaml, hooks/, metadata.json, .gitignore) have
# uncommitted changes — prevents config/hook drift from sneaking past a push.
# grep -v "^??" excludes untracked files; only staged/modified tracked files are checked.
if [ -n "$(git status --porcelain -- .beads/ 2>/dev/null | grep -v '^??')" ]; then
    echo "Uncommitted beads changes detected. Run: git add .beads/config.yaml .beads/hooks/ .beads/metadata.json && git commit"
    exit 1
fi

# Mirror the Dolt DB to the remote before the git push lands.
# Non-blocking: a transient network/auth failure must not wedge an unrelated code push.
timeout 30 bd dolt push || echo "WARNING - bd dolt push failed; beads data was NOT mirrored to the remote. Run \"task beads:push\" manually once it is reachable." >&2
exit 0
