#!/bin/bash
# Post-start hook: cleans stale beads daemon artifacts on every container start.
set -euo pipefail

cd /workspace

if ! command -v bd >/dev/null 2>&1; then
    echo "⚠️  bd not found on PATH; skipping beads startup cleanup"
    exit 0
fi

if [ ! -d ".beads" ]; then
    exit 0
fi

# Fix permissions: Docker bind-mounts (especially on WSL/NTFS) may set group-
# readable bits that bd warns about. Best effort only: startup must continue
# even when stat/chmod is unsupported or the directory is not writable.
beads_mode=""
if beads_mode="$(stat -c '%a' .beads 2>/dev/null)"; then
    if [ "$beads_mode" != "700" ]; then
        chmod 700 .beads 2>/dev/null || true
    fi
fi

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

# Start the beads dolt SQL server.
# bd auto-starts dolt on demand, but starting it explicitly here ensures the
# VSCode extension has a live server to connect to when it initialises.
if command -v dolt >/dev/null 2>&1; then
    if bd dolt start 2>/dev/null; then
        echo "✅ Beads dolt server started"
    fi
    # If the server was already running (second start attempt), the command
    # returns non-zero — that is expected and safe; the 'if' swallows the error.
fi
