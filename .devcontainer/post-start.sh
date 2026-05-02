#!/bin/bash
# Post-start hook: runs tooling startup tasks on every container start.
set -euo pipefail

cd /workspace

# Start Docker daemon (Docker CE baked into image; startup script moved from
# ghcr.io/devcontainers/features/docker-in-docker:2 feature entrypoint).
# Runs only when dockerd is installed and the daemon is not already reachable.
if command -v dockerd >/dev/null 2>&1; then
    if ! docker info >/dev/null 2>&1; then
        echo "🐳 Starting Docker daemon..."
        sudo /usr/local/share/docker-init.sh
        echo "✅ Docker daemon started"
    fi
fi

# Beads startup: only runs when bd is available.
if command -v bd >/dev/null 2>&1; then
    if [ -d ".beads" ]; then
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
else
    echo "⚠️  bd not found on PATH; skipping beads startup cleanup"
fi

# Check GitHub CLI auth — inform but never block startup.
if command -v gh >/dev/null 2>&1; then
    if ! gh auth status >/dev/null 2>&1; then
        echo "ℹ️ GitHub CLI is not authenticated."
        echo "   Run: gh auth login"
    fi
fi
