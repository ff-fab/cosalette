#!/bin/bash
# Post-start hook: ensures the Dolt SQL server is running and the database is
# bootstrapped from JSONL if missing.
#
# Beads uses server mode (dolt sql-server) rather than embedded Dolt to allow
# concurrent access from the CLI, VS Code extension (kanban/graph views), and
# the beads MCP server.  Each devcontainer runs its own local server.
#
# Server lifecycle is delegated to `bd dolt start` which handles port
# selection, PID tracking, and health checks.
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
for artifact in .beads/bd.sock .beads/daemon.pid .beads/daemon.lock; do
    if [ -e "$artifact" ] || [ -S "$artifact" ]; then
        rm -f "$artifact"
        removed=1
    fi
done

if [ "$removed" -eq 1 ]; then
    echo "✅ Cleaned legacy Beads daemon artifacts"
fi

# Remove stale embedded lock (leftover from previous embedded mode)
rm -f /workspace/.beads/embeddeddolt/.lock

# ── Start Dolt SQL server via bd ────────────────────────────────────────────
if ! command -v dolt >/dev/null 2>&1; then
    exit 0
fi

if bd dolt status 2>/dev/null | grep -q "running"; then
    echo "✅ Dolt SQL server already running"
else
    echo "🗃️  Starting Dolt SQL server..."
    if bd dolt start 2>&1; then
        echo "✅ Dolt SQL server started"
    else
        echo "❌ Dolt SQL server failed to start — check .beads/dolt-server.log"
    fi
fi

# ── Bootstrap Dolt database if missing ───────────────────────────────────────
# The Dolt database (.beads/dolt/) is gitignored — it must be rebuilt from
# .beads/issues.jsonl (git-tracked) whenever the container starts on a machine
# that has never had the database created (fresh clone, new PC, CI, etc.).
db_name="beads_COS"
if [ -f ".beads/metadata.json" ]; then
    parsed=$(jq -r '.dolt_database // empty' .beads/metadata.json 2>/dev/null || true)
    [ -n "$parsed" ] && db_name="$parsed"
fi

if ! bd count >/dev/null 2>&1; then
    echo "🔮 Beads database empty or missing — importing from JSONL..."
    bd import && echo "✅ Beads database ready" \
              || echo "❌ bd import failed — run 'bd import' manually"
fi
