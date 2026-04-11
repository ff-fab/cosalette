# Beads: Embedded vs Server Mode Dolt Backend

**Status:** Open — evaluate and decide
**Trigger:** Beads Kanban extension graph view fails with embedded Dolt lock error
**Date:** 2026-04-11

## Problem

The VS Code extension `davidcforbes.beads-kanban` works for kanban and table views
(which use `bd` CLI output), but the **graph view** fails with:

```
Failed to render graph: Failed to get full issue details: bd command failed with
exit code 1: { "error": "failed to open database: embeddeddolt: another process
holds the exclusive lock on /workspace/.beads/embeddeddolt; the embedded backend
supports only one writer }
```

### Root Cause

Beads 1.0.0 uses an **embedded Dolt engine** (`dolt_mode: "embedded"` in
`metadata.json`). Embedded Dolt acquires an exclusive file lock
(`.beads/embeddeddolt/.lock`) that permits only **one writer process at a time**.

When the Kanban extension invokes `bd` commands for the graph view, the embedded
engine tries to acquire the same lock that another `bd` process (or the MCP server
`beads-mcp`) already holds, causing the contention error.

This is not a bug — it is a fundamental limitation of embedded Dolt.

### Constraints

- Must stay on beads 1.0.0
- Must support multi-machine workflow (not multi-user, but same user on different
  dev machines / containers)
- The `.beads/issues.jsonl` file is the git-tracked source of truth for
  cross-machine sync

## Option A: Switch to Server Mode (`bd init --server`)

**What it does:** Runs `dolt sql-server` as a persistent process listening on a MySQL
port (default 3307). All `bd` commands connect via TCP instead of acquiring file locks.

**Implementation:**

1. Add a `dolt sql-server` start to `post-start.sh` (idempotent, background process)
2. Re-initialise beads in server mode: `bd init --server --force --database beads_COS`
3. Update `metadata.json` to `"dolt_mode": "server"`
4. Import from JSONL: `bd import`

**Lifecycle management sketch** (in `post-start.sh`):

```bash
# Start Dolt SQL server if not already running
if ! pgrep -f "dolt sql-server" >/dev/null 2>&1; then
    cd /workspace/.beads/dolt
    nohup dolt sql-server \
        --host 127.0.0.1 \
        --port 3307 \
        --user root \
        --data-dir /workspace/.beads/dolt \
        > /workspace/.beads/dolt-server.log 2>&1 &
    echo $! > /workspace/.beads/dolt-server.pid
fi
```

**Advantages:**

- Solves the lock contention — multiple `bd` processes and the Kanban extension
  can query concurrently
- `dolt sql-server` is a mature, well-tested component (MySQL wire protocol)
- Per-machine lifecycle: server files are gitignored, each machine starts its own
- No change to the cross-machine sync model (still JSONL-based)
- Beads 1.0.0 already supports `--server` flag natively

**Disadvantages:**

- Additional background process to manage (start, health-check, restart)
- Slightly more complex `post-create.sh` / `post-start.sh` scripts
- Need graceful shutdown or the server process becomes orphaned
- Port 3307 must be free (unlikely to conflict, but needs consideration)
- Re-init with `--force` is destructive — must import from JSONL after

**Multi-machine impact:** None — each devcontainer / machine starts its own local
Dolt server. The git-tracked JSONL remains the inter-machine sync mechanism.

## Option B: Shared Server Mode (`bd init --shared-server`)

**What it does:** Runs a single `dolt sql-server` at `~/.beads/shared-server/` that
all projects on the same machine share. Beads configures the database name
per-project (`beads_COS`).

**Implementation:**

1. `bd init --shared-server --force --database beads_COS`
2. Server auto-starts at `~/.beads/shared-server/` (beads manages lifecycle)
3. `bd import`

**Advantages:**

- Beads manages the server lifecycle automatically — less custom scripting
- One server for all repos on the machine (efficient)
- Same JSONL sync model

**Disadvantages:**

- The shared server directory (`~/.beads/shared-server/`) lives **outside** the
  workspace — devcontainer rebuild may not preserve it (depends on volume mounts)
- Less transparent: server management is hidden inside `bd`
- May need home directory persistence between container rebuilds
- Newer feature — less documented

**Multi-machine impact:** Same as Option A — per-machine server, JSONL sync.

## Option C: Read-Only Workaround (No Server)

**What it does:** Keep embedded mode but configure the Kanban extension to use
`--readonly` flag, avoiding write lock contention.

**Implementation:**

1. Check if the Kanban extension supports a "read-only" or "no-write" mode
2. If not, file an issue with the extension author
3. In the meantime, avoid the graph view or close other `bd` processes first

**Advantages:**

- No infrastructure change
- No additional processes

**Disadvantages:**

- May not be possible (extension may not support `--readonly`)
- Graph view would still fail if any write lock is held
- Does not solve the fundamental single-writer limitation
- Fragile — any concurrent `bd` invocation (MCP server, pre-commit hook, agent)
  can trigger the lock error

**Multi-machine impact:** None.

## Option D: Stale Lock Cleanup (Minimal Fix)

**What it does:** Add aggressive lock cleanup to `post-start.sh` and before graph
view invocations.

**Implementation:**

1. Remove `.beads/embeddeddolt/.lock` in `post-start.sh`
2. Check if the lock is stale (no process holding it) before `bd` commands

**Advantages:**

- Minimal change
- Handles stale locks from crashed processes

**Disadvantages:**

- Does NOT solve concurrent access — two legitimate `bd` processes still conflict
- Risk of data corruption if lock is removed while a writer is active
- Only helps when the lock is stale, not when there is genuine contention

**Multi-machine impact:** None.

## Recommendation

**Option A (Server Mode)** is the best fit.

- It directly solves the problem (concurrent access)
- It is a first-class beads feature (`bd init --server`)
- The lifecycle management is straightforward in devcontainer hooks
- Multi-machine workflow is unaffected (JSONL sync continues)
- The gitignore already accounts for server files (`dolt-server.*`)

Option B is a reasonable alternative but the home-directory persistence question
in ephemeral devcontainers makes it riskier.

## Migration Plan (if Option A is chosen)

1. **Update `post-start.sh`** to start `dolt sql-server` if not running
2. **Update `post-create.sh`** to `bd init --server --force --database beads_COS`
   followed by `bd import`
3. **Update `metadata.json`** (automatic via `bd init --server`)
4. **Update `AGENTS.md`** and devcontainer docs to mention server mode
5. **Test:** Verify graph view, kanban view, table view, `bd` CLI, MCP server all
   work concurrently
6. **Test multi-machine:** Clone on a fresh machine, verify bootstrap → server start
   → `bd import` works

## Next Steps

- [ ] Review and approve one of the options
- [ ] Implement migration in a separate PR (infrastructure change)
