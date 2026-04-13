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

# Install dolt — versioned SQL database used by beads (bd) as its backing store.
# Installed at runtime (not in Dockerfile) to avoid Docker layer cache staleness
# and to support retry logic for network flakiness.
install_dolt() {
    local attempts=3
    local n=1
    while [ "$n" -le "$attempts" ]; do
        if curl -fsSL https://github.com/dolthub/dolt/releases/latest/download/install.sh | sudo bash; then
            return 0
        fi
        echo "⚠️  dolt install attempt ${n}/${attempts} failed"
        n=$((n + 1))
        sleep 2
    done
    return 1
}

echo "🗃️  Installing/updating dolt (beads database backend)..."
if install_dolt; then
    hash -r
    echo "✅ dolt $(dolt version 2>/dev/null | head -1)"
else
    echo "❌ Failed to install dolt after multiple attempts"
    exit 1
fi

# Install beads (bd) — git-backed issue tracker for AI agents
# Installed at runtime (not in Dockerfile) to avoid Docker layer cache staleness
# and to support retry logic for network flakiness.
#
# We download the binary directly instead of piping the upstream install.sh to
# bash, because that script's WSL-detection echo statements leak into command
# substitutions and corrupt the download URL (stdout pollution bug).
install_bd() {
    # Ensure fallback install directory exists (CI may not have ~/.local/bin)
    mkdir -p "$HOME/.local/bin"
    local attempts=3
    local n=1
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="amd64" ;;
        aarch64) arch="arm64" ;;
    esac
    # Resolve latest version tag from GitHub redirect
    local latest_url
    latest_url="$(curl -fsSL -o /dev/null -w '%{url_effective}' \
        https://github.com/steveyegge/beads/releases/latest)"
    local version="${latest_url##*/}"          # e.g. "v0.60.0"
    local ver_no_v="${version#v}"              # e.g. "0.60.0"
    local tarball="beads_${ver_no_v}_linux_${arch}.tar.gz"
    local url="https://github.com/steveyegge/beads/releases/download/${version}/${tarball}"

    while [ "$n" -le "$attempts" ]; do
        if curl -fsSL "$url" -o "/tmp/${tarball}" \
            && tar -xzf "/tmp/${tarball}" -C /tmp \
            && { install -m 755 /tmp/bd /usr/local/bin/bd 2>/dev/null \
                || { mkdir -p "$HOME/.local/bin" && install -m 755 /tmp/bd "$HOME/.local/bin/bd"; }; }; then
            rm -f "/tmp/${tarball}" /tmp/bd
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
    # Fix ICU version mismatch: prebuilt bd binary may link against an older ICU
    # than what the container provides (e.g., ICU 74 vs Trixie's ICU 76).
    # Create compatibility symlinks so the binary can load.
    if ! bd version &>/dev/null; then
        echo "⚠️  bd binary has ICU mismatch, creating compatibility symlinks..."
        multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)"
        local_icu=$(ldconfig -p | grep -oP 'libicui18n\.so\.\K[0-9]+' | head -1)
        needed_icu=$(ldd "$(which bd)" 2>/dev/null | grep -oP 'libicui18n\.so\.\K[0-9]+' || true)
        if [ -n "$local_icu" ] && [ -n "$needed_icu" ] && [ "$local_icu" != "$needed_icu" ]; then
            for lib in libicui18n libicuuc libicudata; do
                sudo ln -sf "/lib/${multiarch}/${lib}.so.${local_icu}" \
                            "/lib/${multiarch}/${lib}.so.${needed_icu}" || true
            done
            sudo ldconfig || true
            echo "✅ Symlinked ICU ${needed_icu} → ${local_icu}"
        fi
    fi
    echo "✅ $(bd --version)"
else
    echo "❌ Failed to install bd after multiple attempts"
    exit 1
fi

# Python setup
echo "📦 Setting up Python..."
cd /workspace

# Check if venv exists but has broken symlinks (stale uv cache)
if [ -d ".venv" ]; then
    if ! uv pip check &>/dev/null; then
        echo "⚠️  Detected stale venv (broken symlinks), recreating..."
        rm -rf .venv
    fi
fi

uv sync --all-groups
echo "✅ Python dependencies installed"

# Ensure git is available before git-dependent setup steps.
ensure_git_repo

# Generate version from git tags (setuptools_scm)
echo "📌 Updating version from git tags..."
cd /workspace
uv run --group dev python /workspace/scripts/update_version.py || echo "⚠️  Could not update version (git tags may not be available in this checkout)"

# Pre-download pre-commit hook environments (linters, formatters, etc.)
# NOTE: We use `install-hooks` (not `install --install-hooks`) because beads
# owns Git's hook dispatch via core.hooksPath=.beads/hooks. The beads hooks
# chain to `pre-commit run` after running bd logic. Writing hook shims to
# .git/hooks/ would be silently ignored, so we skip that step entirely.
cd /workspace
if [ -f ".pre-commit-config.yaml" ]; then
    echo "🪝 Caching pre-commit hook environments..."
    if uv run --group dev pre-commit install-hooks; then
        echo "✅ Pre-commit environments cached (hooks chained via .beads/hooks/)"
    else
        echo "⚠️  pre-commit install-hooks had issues, but continuing..."
    fi
fi

# Install beads MCP server for Copilot integration (Python-based)
echo "🔮 Installing beads MCP server..."
uv tool install beads-mcp 2>/dev/null || echo "⚠️  beads-mcp install had issues, continuing..."

# Install showboat — executable demo documents for agent work verification
echo "🚢 Installing showboat..."
uv tool install showboat 2>/dev/null || echo "⚠️  showboat install had issues, continuing..."

# Initialize or bootstrap beads issue tracker
# .beads/ metadata and issues.jsonl are git-tracked, but the Dolt database
# lives in .beads/dolt/ which is gitignored. On a fresh clone or new machine
# the database must be rebuilt from .beads/issues.jsonl.
#
# Beads uses server mode (dolt sql-server) to allow concurrent access from
# the CLI, VS Code extension, and MCP server.  The server is started by
# post-start.sh on every container start.
cd /workspace
_bd_bootstrap_server() {
    local db_name="beads_COS"
    if [ -f ".beads/metadata.json" ]; then
        local parsed
        parsed=$(jq -r '.dolt_database // empty' .beads/metadata.json 2>/dev/null || true)
        [ -n "$parsed" ] && db_name="$parsed"
    fi

    # Start Dolt server via bd (handles port selection, PID, health checks)
    if [ "$(bd dolt status --json 2>/dev/null | jq -r '.running // false')" != "true" ]; then
        echo "🗃️  Starting Dolt SQL server for bootstrap..."
        bd dolt start 2>&1 || true
    fi

    local count
    count=$(bd count 2>/dev/null || echo 0)
    if [ "$count" -gt 0 ] 2>/dev/null; then
        echo "✅ Beads database (${db_name}) already present"
    else
        echo "🔮 Importing beads data from JSONL..."
        if bd import; then
            echo "✅ Beads database bootstrapped from JSONL (${db_name})"
        else
            echo "❌ bd import failed — run 'bd import' manually if bd list fails"
        fi
    fi
}

if [ ! -d ".beads" ]; then
    echo "🔮 Initializing beads issue tracker (server mode)..."
    bd init --server --quiet --skip-hooks
    echo "✅ Beads initialized (server mode)"
else
    _bd_bootstrap_server
fi

# Ensure beads.role is set even if bd init was skipped (e.g. .beads/ already existed)
if ! git config beads.role >/dev/null 2>&1; then
    git config beads.role maintainer
    echo "✅ Set beads.role = maintainer (was missing from git config)"
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
