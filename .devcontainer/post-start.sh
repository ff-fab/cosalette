#!/bin/bash
# Post-start hook: ensures the beads database is bootstrapped from JSONL if
# missing.
#
# Beads uses embedded Dolt mode — no external server needed. The bd binary
# includes its own Dolt engine. This hook just cleans up stale artifacts and
# ensures the database is populated.
set -euo pipefail

cd /workspace

if ! command -v bd >/dev/null 2>&1; then
    echo "⚠️  bd not found on PATH; skipping beads startup"
    exit 0
fi

if [ ! -d ".beads" ]; then
    exit 0
fi

# ── Cleanup stale artifacts ─────────────────────────────────────────────────
removed=0
for artifact in .beads/bd.sock .beads/daemon.pid .beads/daemon.lock .beads/embeddeddolt/.lock; do
    if [ -e "$artifact" ] || [ -S "$artifact" ]; then
        rm -f "$artifact"
        removed=1
    fi
done

if [ "$removed" -eq 1 ]; then
    echo "✅ Cleaned stale Beads artifacts"
fi

# ── Bootstrap database if missing ────────────────────────────────────────────
# The embedded Dolt database (.beads/embeddeddolt/) is gitignored — it must be
# rebuilt from .beads/issues.jsonl (git-tracked) on fresh clones.
count=$(bd count 2>/dev/null || echo 0)
if [ "$count" -eq 0 ] 2>/dev/null; then
    echo "🔮 Beads database empty or missing — importing from JSONL..."
    bd import && echo "✅ Beads database ready" \
              || echo "❌ bd import failed — run 'bd import' manually"
fi
