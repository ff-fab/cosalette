#!/bin/bash
# Update the remote pre-commit hooks (editorconfig, pre-commit-hooks, codespell,
# prettier) to their latest versions. Ruff, ty and detect-secrets are local hooks
# that run the uv.lock versions; update those with `uv lock --upgrade-package`.

set -e
cd "$(git rev-parse --show-toplevel)"

if [ ! -f ".pre-commit-config.yaml" ]; then
    echo "❌ No .pre-commit-config.yaml found in workspace root"
    exit 1
fi

echo "🔄 Updating pre-commit hooks to latest versions..."
# UV_FROZEN=1 keeps uv from rewriting uv.lock as a side effect (cos-e447)
UV_FROZEN=1 uv run --group dev pre-commit autoupdate

echo ""
echo "✅ Pre-commit hooks updated!"
echo ""
echo "📋 Review changes with: git diff .pre-commit-config.yaml"
echo "🧪 Test hooks with:     pre-commit run --all-files"
