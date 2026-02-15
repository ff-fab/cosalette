#!/bin/bash
# Post-create setup script for devcontainer
set -e

export PATH="/home/vscode/.local/bin:$PATH"

ensure_git_repo() {
    local repo_root="/workspace"

    if ! command -v git >/dev/null 2>&1; then
        echo "❌ git is required but not installed."
        return 1
    fi

    if git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return 0
    fi

    echo "❌ No Git repository found at ${repo_root}."
    echo "   Git-dependent setup cannot continue."
    echo "   Initialize one now with: git -C ${repo_root} init -b main"
    echo "   Tip: during scaffolding, set 'init_git_on_copy' to true to do this automatically."
    return 1
}

echo "🏠 Setting up cosalette development environment..."

install_bd() {
    local attempts=3
    local n=1
    while [ "$n" -le "$attempts" ]; do
        if curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash; then
            return 0
        fi
        echo "⚠️  bd install attempt ${n}/${attempts} failed"
        n=$((n + 1))
        sleep 2
    done
    return 1
}

echo "🔮 Installing/updating beads CLI..."
if install_bd; then
    hash -r
    echo "✅ $(bd --version)"
else
    echo "❌ Failed to install bd after multiple attempts"
    exit 1
fi

# Python setup
echo "📦 Setting up Python..."
cd /workspace/packages

# Check if venv exists but has broken symlinks (stale uv cache)
if [ -d ".venv" ]; then
    if ! uv pip check &>/dev/null; then
        echo "⚠️  Detected stale venv (broken symlinks), recreating..."
        rm -rf .venv
    fi
fi

uv sync --all-extras
echo "✅ Python dependencies installed"

# Ensure git is available before git-dependent setup steps.
ensure_git_repo

# Generate version from git tags (setuptools_scm)
echo "📌 Updating version from git tags..."
cd /workspace
uv --directory packages run --group dev python /workspace/scripts/update_version.py || echo "⚠️  Could not update version (git tags may not be available in this checkout)"

# Install pre-commit hooks (if configured)
cd /workspace
if [ -f ".pre-commit-config.yaml" ]; then
    echo "🪝 Installing pre-commit hooks..."
    # Use uv --directory to specify the Python environment without changing directories
    # This runs pre-commit from the repository root (where .pre-commit-config.yaml is)
    if uv --directory packages run --group dev pre-commit install --install-hooks; then
        echo "✅ Pre-commit hooks installed successfully"
    else
        echo "⚠️  pre-commit install had issues, but continuing..."
    fi
    # Install additional hook stages for beads (bd) sync
    uv --directory packages run --group dev pre-commit install --hook-type pre-push --hook-type post-merge 2>/dev/null || true
fi

# Install beads MCP server for Copilot integration (Python-based)
echo "🔮 Installing beads MCP server..."
uv tool install beads-mcp 2>/dev/null || echo "⚠️  beads-mcp install had issues, continuing..."

# Install showboat — executable demo documents for agent work verification
echo "🚢 Installing showboat..."
uv tool install showboat 2>/dev/null || echo "⚠️  showboat install had issues, continuing..."

# Initialize beads issue tracker if not already done
cd /workspace
if [ ! -d ".beads" ]; then
    echo "🔮 Initializing beads issue tracker..."
    bd init --quiet --skip-hooks
    echo "✅ Beads initialized"
else
    echo "✅ Beads already initialized"
fi

# SSH: seed known_hosts for GitHub so the first git push doesn't trigger a TOFU prompt.
# VS Code forwards the host's SSH agent automatically (SSH_AUTH_SOCK), so keys never
# enter the container. We just need known_hosts to be pre-populated and writable.
mkdir -p /home/vscode/.ssh
chmod 700 /home/vscode/.ssh
ssh-keyscan -t ed25519 github.com >> /home/vscode/.ssh/known_hosts 2>/dev/null
chmod 644 /home/vscode/.ssh/known_hosts
chown -R vscode:vscode /home/vscode/.ssh
echo "✅ SSH known_hosts seeded (agent forwarding handles authentication)"

# GitHub CLI: disable pager (prevents 'alternate buffer' issues with Copilot in VS Code)
# gh defaults to $PAGER (=less) when its own pager config is blank.
# GH_PAGER=cat is set via remoteEnv, but gh config persists across shell sessions.
gh config set pager cat 2>/dev/null || true

# GitHub CLI authentication reminder
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ DevContainer ready! Development environment configured."
echo ""
echo "🔧 Maintenance:"
echo "   Update pre-commit hooks: ./scripts/update-precommit.sh"
echo ""
echo "GitHub CLI: Run 'gh auth login' if needed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
