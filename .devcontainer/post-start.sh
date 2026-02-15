#!/bin/bash
set -euo pipefail

cd /workspace

if ! command -v bd >/dev/null 2>&1; then
    echo "⚠️  bd not found on PATH; skipping beads startup cleanup"
    exit 0
fi

if [ ! -d ".beads" ]; then
    exit 0
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
