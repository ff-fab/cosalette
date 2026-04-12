# Beads Setup Guide for Copier Template

Battle-tested guide for integrating **bd (beads)** into new projects via the copier
template. Based on lessons learned from the cosalette project cleanup.

- **bd version:** latest (install resolves `releases/latest`; guide tested with 1.0.0)
- **Backend:** Dolt SQL server (`dolt sql-server` managed by `bd dolt start`)
- **VS Code extension:** `davidcforbes.beads-kanban` (latest; guide tested with v2.1.2)

## Contents

- [1. Overview](#1-overview)
  - [1a. Prerequisites](#1a-prerequisites)
- [2. Master Data](#2-master-data-source-of-truth)
- [3. Canonical .gitignore](#3-canonical-gitignore)
- [4. metadata.json Template](#4-metadatajson-template)
- [5. config.yaml Template](#5-configyaml-template)
- [6. Devcontainer Integration](#6-devcontainer-integration)
- [7. Git Hooks](#7-git-hooks)
- [8. Common Pitfalls](#8-common-pitfalls)
- [9. Verification Checklist](#9-verification-checklist)
- [10. Clean Setup From Scratch](#10-clean-setup-from-scratch-reference)

## 1. Overview

Beads is a git-backed graph issue tracker designed for AI agents. It stores issues in a
local Dolt SQL database for fast querying and concurrent access, while using JSONL as the
git-tracked source of truth.

Key architectural decisions:

- **Dolt SQL server** is the database backend (not embedded mode, not SQLite).
- **`issues.jsonl`** is the canonical git-tracked export — the single source of truth.
- **The Dolt database is ephemeral** — rebuilt from JSONL on each machine via
  `bd import`.
- The daemon was **removed** in bd ≥0.50. All access goes through the Dolt SQL server.

## 1a. Prerequisites

The bootstrap scripts require these tools on `PATH`:

| Tool   | Used for                          | Typical install             |
| ------ | --------------------------------- | --------------------------- |
| `bash` | All scripts                       | Pre-installed on Linux      |
| `curl` | Downloading Dolt and bd releases  | `apt install curl`          |
| `jq`   | Parsing JSON (metadata, status)   | `apt install jq`            |
| `tar`  | Extracting bd release tarball     | Pre-installed on Linux      |
| `sudo` | Installing Dolt system-wide       | Pre-installed on Linux      |

In a devcontainer, ensure the base image or a feature installs `jq`. The default
`mcr.microsoft.com/devcontainers/base` images include it.

## 2. Master Data (Source of Truth)

These are the **only** files that should be git-tracked under `.beads/`:

| File            | Purpose                                                    |
| --------------- | ---------------------------------------------------------- |
| `issues.jsonl`  | All issues — canonical export from Dolt DB                 |
| `config.yaml`   | Team-wide beads configuration (issue prefix, etc.)         |
| `metadata.json` | Project identity: database name, backend, dolt_mode, UUID  |
| `hooks/`        | Git hook scripts managed by beads (pre-push, pre-commit…)  |
| `README.md`     | Documentation                                              |
| `.gitignore`    | Prevents ephemeral files from being tracked                |

Verify with:

```bash
git ls-files .beads/
```

Expected output (and nothing else):

```text
.beads/.gitignore
.beads/README.md
.beads/config.yaml
.beads/hooks/post-checkout
.beads/hooks/post-merge
.beads/hooks/pre-commit
.beads/hooks/pre-push
.beads/hooks/prepare-commit-msg
.beads/issues.jsonl
.beads/metadata.json
```

> **CRITICAL:** `metadata.json` MUST have `"dolt_mode": "server"`. If it says
> `"embedded"`, the VS Code extension and `bd doctor` will not work correctly. This was
> a bug discovered during the cosalette cleanup.

## 3. Canonical .gitignore

Place this file at `.beads/.gitignore`. The header comment documents the tracked vs
ephemeral split:

```gitignore
# ============================================================
# Beads .gitignore — canonical for {{PROJECT_NAME}}
# ============================================================
# TRACKED (source of truth):
#   issues.jsonl     — all issues exported from Dolt DB
#   config.yaml      — team-wide beads configuration
#   metadata.json    — project identity (database name, backend)
#   hooks/           — git hook scripts managed by beads
#   README.md        — documentation
#   .gitignore       — this file
#
# EVERYTHING ELSE is ephemeral / machine-local and must NOT
# be committed.
# ============================================================

# ── Dolt database (rebuilt from issues.jsonl on each machine) ──
dolt/

# ── Legacy embedded Dolt (removed in bd ≥1.0) ─────────────────
embeddeddolt/

# ── Backup directories (auto-generated, contain .darc blobs) ──
backup/
backup.bak/

# ── Dolt server runtime ───────────────────────────────────────
dolt-server.pid
dolt-server.log
dolt-server.lock
dolt-server.port
dolt-server.activity

# ── Beads runtime state ───────────────────────────────────────
bd.sock
bd.sock.startlock
sync-state.json
push-state.json
last-touched
interactions.jsonl
.exclusive-lock
.local_version

# ── Daemon artifacts (legacy, removed in bd ≥0.50) ────────────
daemon.*

# ── Lock files ────────────────────────────────────────────────
*.lock

# ── Credential key (encryption key — NEVER commit) ────────────
.beads-credential-key

# ── Worktree redirect (machine-specific path) ─────────────────
redirect

# ── Sync/export state (per-machine) ──────────────────────────
.sync.lock
export-state/
export-state.json

# ── Ephemeral store (wisps/molecules, not versioned) ──────────
ephemeral.sqlite3
ephemeral.sqlite3-journal
ephemeral.sqlite3-wal
ephemeral.sqlite3-shm

# ── Corrupt backup recovery dirs ─────────────────────────────
*.corrupt.backup/

# ── Per-project env file ──────────────────────────────────────
.env

# ── Legacy SQLite databases (pre-Dolt versions) ──────────────
*.db
*.db?*
*.db-journal
*.db-wal
*.db-shm
db.sqlite
bd.db
```

## 4. metadata.json Template

```json
{
  "database": "dolt",
  "backend": "dolt",
  "dolt_mode": "server",
  "dolt_database": "beads_{{ISSUE_PREFIX}}",
  "project_id": "{{UUID}}"
}
```

| Field           | Value                                                        |
| --------------- | ------------------------------------------------------------ |
| `dolt_mode`     | **Must be `"server"`** — never `"embedded"`                  |
| `dolt_database` | Uses the issue prefix from `config.yaml` (e.g., `beads_COS`) |
| `project_id`    | UUID generated by `bd init`                                  |

> **WARNING:** If `bd dolt start` is run on a fresh directory before `metadata.json`
> exists, bd may write `"dolt_mode": "embedded"` as the default. The template must
> ship `metadata.json` pre-configured with `"server"` mode.

## 5. config.yaml Template

```yaml
issue-prefix: '{{PREFIX}}'
```

The prefix is typically the project name in lowercase, 3–5 characters (e.g., `cos` for
cosalette). It determines issue IDs like `COS-1hf`, `COS-cjg`, etc. (suffixes are
alphanumeric, not sequential).

> **Note:** `{{PREFIX}}` is the lowercase prefix (e.g., `cos`). `{{ISSUE_PREFIX}}` is
> the uppercased form used in database names and issue IDs (e.g., `COS`).

## 6. Devcontainer Integration

### post-create.sh (runs once when container is built)

Extract the beads-relevant portions into `post-create.sh`. The script must:

1. Install Dolt (from GitHub releases, with retry logic)
2. Install bd (from GitHub releases, with retry logic and ICU compatibility shim)
3. Handle ICU version mismatch between prebuilt bd binary and container's ICU
4. Bootstrap the Dolt server and import from JSONL if needed

```bash
#!/bin/bash
set -e
export PATH="/home/vscode/.local/bin:$PATH"

# ── Install Dolt ────────────────────────────────────────────────────────────
install_dolt() {
    local attempts=3
    local n=1
    while [ "$n" -le "$attempts" ]; do
        if curl -fsSL -m 60 https://github.com/dolthub/dolt/releases/latest/download/install.sh | sudo bash; then
            return 0
        fi
        echo "⚠️  dolt install attempt ${n}/${attempts} failed"
        n=$((n + 1))
        sleep 2
    done
    return 1
}

# ⚠️  SECURITY NOTE: This pipes a remote script to sudo bash — the standard
# Dolt install method. For hardened environments, download install.sh first,
# review it, and run it separately. Upstream does not publish checksums.

echo "🗃️  Installing/updating dolt (beads database backend)..."
if install_dolt; then
    hash -r
    echo "✅ dolt $(dolt version 2>/dev/null | head -1)"
else
    echo "❌ Failed to install dolt after multiple attempts"
    exit 1
fi

# NOTE: Dolt and bd downloads are independent and could be parallelized
# with background processes for ~30% faster container builds.

# ── Install bd (beads CLI) ──────────────────────────────────────────────────
# Download binary directly instead of piping the upstream install.sh to bash,
# because that script's WSL-detection echo statements leak into command
# substitutions and corrupt the download URL (stdout pollution bug).
install_bd() {
    mkdir -p "$HOME/.local/bin"
    # Phase 1: Detect architecture
    local attempts=3
    local n=1
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="amd64" ;;
        aarch64) arch="arm64" ;;
    esac
    # Phase 2: Resolve latest version URL
    local latest_url
    latest_url="$(curl -fsSL -m 60 -o /dev/null -w '%{url_effective}' \
        https://github.com/steveyegge/beads/releases/latest)"
    # ⚠️  SECURITY NOTE: Binary downloaded without checksum verification.
    # Upstream does not currently publish checksums. For hardened environments,
    # mirror the artifact internally and verify with your own checksums.
    local version="${latest_url##*/}"
    local ver_no_v="${version#v}"
    local tarball="beads_${ver_no_v}_linux_${arch}.tar.gz"
    local url="https://github.com/steveyegge/beads/releases/download/${version}/${tarball}"

    # Phase 3: Download and install with retries
    while [ "$n" -le "$attempts" ]; do
        if curl -fsSL -m 60 "$url" -o "/tmp/${tarball}" \
            && tar -xzf "/tmp/${tarball}" -C /tmp \
            && { install -m 755 /tmp/bd /usr/local/bin/bd 2>/dev/null \
                || { mkdir -p "$HOME/.local/bin" \
                     && install -m 755 /tmp/bd "$HOME/.local/bin/bd"; }; }; then
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
    # Fix ICU version mismatch: prebuilt bd binary may link against an older
    # ICU than what the container provides (e.g., ICU 74 vs Trixie's ICU 76).
    # ICU check is lazy — only runs when bd version fails (once per container build)
    if ! bd version &>/dev/null; then
        echo "⚠️  bd binary has ICU mismatch, creating compatibility symlinks..."
        multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null \
            || echo x86_64-linux-gnu)"
        local_icu=$(ldconfig -p \
            | grep -oP 'libicui18n\.so\.\K[0-9]+' | head -1)
        needed_icu=$(ldd "$(which bd)" 2>/dev/null \
            | grep -oP 'libicui18n\.so\.\K[0-9]+' || true)
        if [ -n "$local_icu" ] && [ -n "$needed_icu" ] \
            && [ "$local_icu" != "$needed_icu" ]; then
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

# ── Bootstrap beads ─────────────────────────────────────────────────────────
cd /workspace

_bd_bootstrap_server() {
    local db_name="beads_{{ISSUE_PREFIX}}"
    if [ -f ".beads/metadata.json" ]; then
        local parsed
        parsed=$(jq -r '.dolt_database // empty' .beads/metadata.json 2>/dev/null || true)
        [ -n "$parsed" ] && db_name="$parsed"
    fi

    if [ "$(bd dolt status --json 2>/dev/null | jq -r '.running // false' 2>/dev/null)" != "true" ]; then
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

# Ensure beads.role is set
if ! git config beads.role >/dev/null 2>&1; then
    git config beads.role maintainer
fi
```

### post-start.sh (runs every time container starts)

```bash
#!/bin/bash
# Ensures the Dolt SQL server is running and the database is bootstrapped.
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

if [ "$(bd dolt status --json 2>/dev/null | jq -r '.running // false' 2>/dev/null)" = "true" ]; then
    echo "✅ Dolt SQL server already running"
else
    echo "🗃️  Starting Dolt SQL server..."
    if bd dolt start 2>&1; then
        echo "✅ Dolt SQL server started"
    else
        echo "❌ Dolt SQL server failed to start — check .beads/dolt-server.log"
    fi
fi

# ── Bootstrap Dolt database if missing ──────────────────────────────────────
count=$(bd count 2>/dev/null || echo 0)
if [ "$count" -eq 0 ] 2>/dev/null; then
    echo "🔮 Beads database empty or missing — importing from JSONL..."
    bd import && echo "✅ Beads database ready" \
              || echo "❌ bd import failed — run 'bd import' manually"
fi
```

### devcontainer.json settings

Include the beads-kanban extension. Do **not** include any daemon-related settings:

```jsonc
{
  "customizations": {
    "vscode": {
      "settings": {
        // NO beads.autoStartDaemon setting — daemon was removed in bd ≥0.50.
        // Including it is a no-op and causes confusion.
      },
      "extensions": [
        "davidcforbes.beads-kanban"
      ]
    }
  },
  "postCreateCommand": ".devcontainer/post-create.sh",
  "postStartCommand": ".devcontainer/post-start.sh"
}
```

> **IMPORTANT:** Do NOT include `"beads.autoStartDaemon": false`. The daemon was removed
> in bd 0.50. This setting is a no-op and causes confusion when debugging.

## 7. Git Hooks

Beads ships five hook scripts:

| Hook                   | Purpose                                      |
| ---------------------- | -------------------------------------------- |
| `pre-push`             | Validates JSONL is synced before push         |
| `pre-commit`           | Sync check on commit                         |
| `post-checkout`        | Refresh state after branch switch             |
| `post-merge`           | Refresh state after merge                     |
| `prepare-commit-msg`   | Inject beads context into commit messages     |

Two approaches:

1. **Run `bd hooks install` in `post-create.sh`** — hooks are generated at container
   build time. Simpler, but requires `bd` to be installed first.
2. **Ship pre-built hooks in `.beads/hooks/`** — the hooks are committed to git and
   always available. This is what cosalette does.

For the template, **ship pre-built hooks** (option 2). Copy them from a working project
after running `bd hooks install`, then commit `.beads/hooks/` to git.

## 8. Common Pitfalls

### 8.1 Binary .darc files in git

Dolt auto-backup creates `.darc` binary blobs in `.beads/backup/`. If `.gitignore`
patterns are wrong, these get committed and bloat the repo. The cosalette project
accumulated 7.5 MB of these that had to be purged with `git-filter-repo`.

**Prevention:** Ensure `backup/` and `backup.bak/` are in `.beads/.gitignore` from day
one. Verify with `git ls-files .beads/ | grep -c darc` — must return 0.

### 8.2 metadata.json mode mismatch

If `dolt_mode` is `"embedded"` instead of `"server"`, `bd doctor` fails and the VS Code
extension cannot connect. Always set `"server"`.

**Fix:** Edit `.beads/metadata.json` and change `"dolt_mode"` to `"server"`.

### 8.3 VS Code extension daemon error

The error:

```text
Daemon is not running: Unexpected token 'B', "Beads Data"... is not valid JSON
```

Means the extension is trying to use the removed daemon protocol.

**Fix:** Ensure `dolt_mode` is `"server"` in `metadata.json` and that the Dolt server
is running (`bd dolt start`). Reload the VS Code window after starting the server.

### 8.4 Stale embeddeddolt directory

Legacy from pre-1.0 bd. Can be safely deleted. Already in `.gitignore`.

```bash
rm -rf .beads/embeddeddolt/
```

### 8.5 backup.bak tracked in git

If the backup directory was ever committed, the `.darc` files inside will bloat the repo
permanently (even after deleting them in a new commit — they remain in git history).

**Fix:** Use `git filter-repo` to purge:

```bash
git filter-repo --invert-paths --path-glob '.beads/backup*/**'
```

### 8.6 Dolt auto-backup 401 error

If bd commands emit:

```text
Warning: auto-backup failed: … strconv.ParseUint: parsing "401\n": invalid syntax
```

The backup directory has a stale manifest written by a different Dolt version.

**Fix:**

```bash
rm -rf .beads/backup && mkdir -p .beads/backup
```

Dolt auto-backup will recreate the directory on the next write.

### 8.7 Fresh clone bootstrap

On a fresh clone, the Dolt DB does not exist. `post-start.sh` must run `bd import` to
hydrate from `issues.jsonl`. If `post-start.sh` did not run (e.g., outside a
devcontainer):

```bash
bd dolt start
bd import
```

### 8.8 bd dolt start defaults to embedded on fresh directory

When starting Dolt on a directory that has no prior Dolt data, bd may write
`"dolt_mode": "embedded"` to `metadata.json`. The template **must** ship
`metadata.json` pre-configured with `"server"` mode so this default is never applied.

## 9. Verification Checklist

After setting up beads in a new project, verify:

- [ ] `bd dolt status` shows server running
- [ ] `bd count` returns expected issue count (or 0 for a new project)
- [ ] `bd doctor` passes with no errors (warnings about upstream are OK for new branches)
- [ ] `git ls-files .beads/` shows ONLY the master data files listed in section 2
- [ ] No `.darc` files in `git ls-files`
- [ ] VS Code beads-kanban extension shows the board (reload window if extension loaded
      before server started)

## 10. Clean Setup From Scratch (Reference)

For existing projects that need a reset:

```bash
# 1. Back up master data
cp .beads/issues.jsonl /tmp/beads-backup/
cp .beads/config.yaml /tmp/beads-backup/
cp .beads/metadata.json /tmp/beads-backup/

# 2. Stop server and nuke ephemeral state
bd dolt stop
rm -rf .beads/dolt/ .beads/embeddeddolt/ .beads/backup/ .beads/backup.bak/
rm -f .beads/dolt-server.* .beads/ephemeral.sqlite3 .beads/last-touched
rm -f .beads/push-state.json .beads/sync-state.json .beads/interactions.jsonl
rm -f .beads/.beads-credential-key .beads/.local_version
rm -rf .beads/export-state/

# 3. Ensure metadata.json has dolt_mode: "server"
jq '.dolt_mode = "server"' .beads/metadata.json > /tmp/meta.json \
    && mv /tmp/meta.json .beads/metadata.json

# 4. Restart and import
bd dolt start
bd import
bd doctor
bd count  # should match JSONL line count
```
