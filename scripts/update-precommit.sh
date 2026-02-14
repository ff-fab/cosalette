#!/bin/bash
# Update pre-commit hooks to their latest versions
# Run this periodically to keep linting tools (ruff, mypy, etc.) current

set -e
cd /workspace

if [ ! -f ".pre-commit-config.yaml" ]; then
    echo "❌ No .pre-commit-config.yaml found in workspace root"
    exit 1
fi

echo "🔄 Updating pre-commit hooks to latest versions..."
uv --directory packages run --group dev pre-commit autoupdate

echo ""
echo "✅ Pre-commit hooks updated!"
echo ""
echo "📋 Review changes with: git diff .pre-commit-config.yaml"
echo "🧪 Test hooks with:     pre-commit run --all-files"
