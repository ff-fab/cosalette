#!/bin/bash
# Post-start hook: cleans stale beads daemon artifacts and bootstraps the Dolt
# database from git-tracked JSONL if it is missing (e.g. on a new machine).
set -euo pipefail

cd /workspace

if ! command -v bd >/dev/null 2>&1; then
    echo "⚠️  bd not found on PATH; skipping beads startup"
    exit 0
fi

if [ ! -d ".beads" ]; then
    exit 0
fi

# ── Cleanup stale daemon artifacts ──────────────────────────────────────────
removed=0
if [ -S ".beads/bd.sock" ]; then
    rm -f .beads/bd.sock
    removed=1
fi

if [ -f ".beads/daemon.pid" ]; then
    rm -f .beads/daemon.pid
    removed=1
fi

if [ -f ".beads/daemon.lock" ]; then
    rm -f .beads/daemon.lock
    removed=1
fi

if [ "$removed" -eq 1 ]; then
    echo "✅ Cleaned legacy Beads daemon artifacts"
fi

# ── Bootstrap Dolt database if missing ───────────────────────────────────────
# The Dolt database (.beads/dolt/) is gitignored — it must be rebuilt from
# .beads/issues.jsonl (git-tracked) whenever the container starts on a machine
# that has never had the database created (fresh clone, new PC, CI, etc.).
if ! command -v dolt >/dev/null 2>&1; then
    exit 0
fi

db_name=$(grep -o '"dolt_database":"[^"]*"' .beads/metadata.json \
          | grep -o '"[^"]*"$' | tr -d '"' 2>/dev/null)
db_name="${db_name:-beads_COS}"

if [ ! -d ".beads/dolt/${db_name}" ]; then
    echo "🔮 Beads Dolt database (${db_name}) missing — rebuilding from JSONL..."
    mkdir -p .beads/dolt
    (cd .beads/dolt && dolt sql -q "CREATE DATABASE \`${db_name}\`;" 2>/dev/null) || true
    bd import && echo "✅ Beads database (${db_name}) ready" \
              || echo "❌ bd import failed — run 'bd import' manually"
fi
