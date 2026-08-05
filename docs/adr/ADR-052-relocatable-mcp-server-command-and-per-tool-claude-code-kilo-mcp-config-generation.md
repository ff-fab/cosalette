---
status: Accepted
date: 2026-08-05
impact: moderate
tags: [cli, packaging, configuration, security]
---

# ADR-052: Relocatable MCP Server Command and Per-Tool (Claude Code / Kilo) MCP Config Generation

## Status

Accepted **Date:** 2026-08-05

## Context

`_manage_mcp_config()` in `packages/src/cosalette/_package_cli/_json_config.py` writes `.vscode/mcp.json` whenever `cosalette[mcp]` (fastmcp) is importable, and is called unconditionally from `cosalette ai init` in `packages/src/cosalette/_package_cli/__init__.py`. It currently hardcodes `"command": sys.executable` -- an absolute interpreter path that is only valid for the checkout/venv where `ai init` was run. If the generated `.vscode/mcp.json` is committed or shared, or the repo is checked out at a different path or on a different host, the baked-in interpreter path no longer resolves and the MCP server fails to launch.

ADR-035 (`docs/adr/ADR-035-optional-mcp-layer-for-downstream-ai-support.md`) established the optional MCP layer and its VS Code-only `.vscode/mcp.json` scope, but did not address relocatability of the launch command, and did not extend MCP config generation to other AI-assisted editors.

MCP configuration for other tools is currently hand-maintained rather than generated: Claude Code reads an `mcpServers` map from a root `.mcp.json`; Kilo reads an `mcp` block from `kilo.jsonc` with entries shaped like `{"type": "local"|"remote", "command": ..., "args": ...}`. Kilo's exact MCP schema is inferred from the originating issue description rather than verified against official Kilo documentation or an in-repo reference, since none exists in this repository.

`ai init` already supports a `--kilo` boolean flag (routing to `_manage_kilo_config`, which today only manages Kilo's `instructions` array, not MCP) and an `--opencode` flag (same pattern, targeting `opencode.json`). There is no `--claude` flag yet. `_merge_mcp_server_config` and `_mcp_paths_are_safe` already implement non-destructive merge and symlink-safety (CWE-59) handling for `.vscode/mcp.json`, and are candidates for reuse against the new targets.

This work was deferred as beads issue cos-wfm8 ("Deferred (F5): relocatable MCP command + per-tool (--claude/--kilo) MCP targets") because the right relocatable command was genuinely debatable. The command construction approach below has since been discussed and approved by the maintainer.

## Decision

Replace the hardcoded `sys.executable` command in `_manage_mcp_config()` with a relocatable invocation -- preferring `uv run --package cosalette python -m cosalette ai mcp serve` when `uv` is on PATH and a uv-managed workspace is detected, falling back to a PATH-resolved `python3 -m cosalette ai mcp serve` otherwise -- because this project mandates `uv`/`task` over bare `python` invocations, and apply the same relocatable-command logic to two new generation targets: a `--claude` flag on `ai init` that writes `mcpServers.cosalette` into a root `.mcp.json`, and an extension of the existing `--kilo` flag that writes an `mcp.cosalette` entry into `kilo.jsonc`, both merged non-destructively using the same merge/symlink-safety treatment as `.vscode/mcp.json`.

```python
def _relocatable_mcp_command(repo_root: Path) -> dict[str, object]:
    """Build a relocatable MCP launch command, preferring uv when available."""
    if shutil.which("uv") and _is_uv_workspace(repo_root):
        return {
            "command": "uv",
            "args": [
                "run", "--package", "cosalette",
                "python", "-m", "cosalette", "ai", "mcp", "serve",
            ],
        }
    # PATH-resolved at invocation time -- not baked from sys.executable.
    return {
        "command": "python3",
        "args": ["-m", "cosalette", "ai", "mcp", "serve"],
    }


def _is_uv_workspace(repo_root: Path) -> bool:
    """Return True when repo_root looks like a uv-managed workspace."""
    return (repo_root / "uv.lock").exists() or (repo_root / "pyproject.toml").exists()
```

## Decision Drivers

- The existing hardcoded `sys.executable` command breaks the moment `.vscode/mcp.json` is committed, shared, or used from a different clone path or host checkout -- it is only valid for the exact venv/checkout that ran `ai init`.
- `.github/instructions/tooling.instructions.md` mandates `task`/`uv` over bare `python` invocations project-wide, so the relocatable-command choice should prefer `uv run` when available rather than introduce a third, inconsistent invocation style.
- MCP configuration for Claude Code (`.mcp.json`) and Kilo (`kilo.jsonc`) is currently hand-maintained and therefore prone to drifting from cosalette's actual invocation contract as the command construction evolves.
- `ai init` already establishes a per-tool boolean-flag pattern (`--opencode`, `--kilo`) that a `--claude` flag and an extended `--kilo` path can follow directly, minimizing new CLI surface.
- Existing `_merge_mcp_server_config` / `_mcp_paths_are_safe` helpers already solve non-destructive merging and symlink safety (CWE-59) for `.vscode/mcp.json` and are directly reusable for the new targets, keeping the change additive rather than a rewrite.

## Considered Options

### Option 1: uv run with python3 PATH fallback (chosen)

Detect `uv` on PATH and a uv-managed workspace (a `pyproject.toml`/`uv.lock` at repo root); when both are present, emit `command: "uv"`, `args: ["run", "--package", "cosalette", "python", "-m", "cosalette", "ai", "mcp", "serve"]`. Otherwise emit `command: "python3"`, `args: ["-m", "cosalette", "ai", "mcp", "serve"]`, resolved from PATH at invocation time rather than baked from `sys.executable`. Applied uniformly to `.vscode/mcp.json`, `.mcp.json`, and `kilo.jsonc`.

- *Advantages:* Fully relocatable: no absolute interpreter path is baked into any checked-in config file; Consistent with the project's uv/task-first tooling convention documented in tooling.instructions.md; Degrades gracefully outside a uv-managed workspace via the python3 fallback, so it still works in plain-venv or system-Python setups
- *Disadvantages:* uv run adds minor per-invocation startup overhead compared to a direct interpreter call, paid on every MCP server launch by an IDE/agent; Two code paths (uv-preferred vs python3-fallback) add branching and a workspace-detection helper to _manage_mcp_config and its new callers; The python3 fallback depends on the correct environment being first on PATH at invocation time -- a misconfigured PATH can select a python3 that lacks cosalette installed

### Option 2: Bare python3 only, no uv detection

Always emit `command: "python3"`, `args: ["-m", "cosalette", "ai", "mcp", "serve"]`, regardless of whether `uv` is available, with no workspace-detection logic.

- *Advantages:* Single code path -- simplest possible implementation with no uv-detection helper needed; Still relocatable: PATH-resolved at invocation time rather than baked from sys.executable; Identical behavior across uv-managed and non-uv environments, which is easy to reason about and document
- *Disadvantages:* Ignores the project's uv/task-first tooling convention despite tooling.instructions.md mandating it for this exact class of invocation; Relies on whatever python3 resolves to first on PATH containing cosalette, which is not guaranteed outside an activated venv or `uv run` shell; Loses the opportunity to prefer the project's own dependency-isolation mechanism (uv) when it is demonstrably available

### Option 3: Keep hardcoded sys.executable (status quo)

Leave `_manage_mcp_config()` unchanged, continuing to bake the absolute interpreter path active at `ai init` time into `.vscode/mcp.json`, and leave Claude Code and Kilo MCP config hand-maintained with no generation support.

- *Advantages:* Zero implementation effort -- no code change required; Correct for the exact single checkout/venv that ran ai init, with no detection logic needed; No risk of introducing a new bug in command construction
- *Disadvantages:* Breaks the moment the generated config is committed, shared, or used from a different clone path or host checkout -- this is the exact defect cos-wfm8 was filed to fix; Leaves Claude Code and Kilo MCP config permanently hand-maintained, prone to drifting from cosalette's actual invocation contract; Does not use uv despite the project's own tooling convention mandating it over bare python invocations

## Decision Matrix

| Criterion | uv run with python3 PATH fallback | Bare python3 only, no uv detection | Keep hardcoded sys.executable (status quo) |
| --- | --- | --- | --- |
| Relocatability across checkouts/hosts | 5 | 4 | 1 |
| Alignment with project uv/task tooling convention | 5 | 2 | 1 |
| Works correctly without uv installed | 5 | 5 | 5 |
| Implementation complexity (inverted: 5=low) | 3 | 5 | 5 |
| Per-invocation startup overhead (inverted: 5=low) | 3 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- `.vscode/mcp.json`, `.mcp.json`, and `kilo.jsonc` become portable across clones and host checkouts since no absolute interpreter path is baked into any of them
- All three generation targets share a single relocatable-command construction, keeping the invocation contract single-sourced instead of triplicated
- Adding `--claude` and extending `--kilo` is purely additive -- no `--claude`/`--kilo` MCP writing happens unless the corresponding flag is passed, so there is no behavior change for existing users beyond flags they already know from instruction-file management
- Generated MCP configs now align with the project's own uv/task-first tooling convention rather than an ad hoc absolute path
- Reuses the existing non-destructive merge and symlink-safety (CWE-59) treatment from `_merge_mcp_server_config`/`_mcp_paths_are_safe` for the new targets instead of duplicating that logic

### Negative

- Kilo's exact MCP schema (the `type`/`command`/`args` shape under `mcp.cosalette`) is inferred from the originating issue description, not verified against official Kilo documentation or an in-repo reference, and may need adjustment once verified against real Kilo behavior
- `uv run` adds minor per-invocation startup overhead relative to a direct interpreter call, paid on every MCP server launch by an IDE or agent in a uv-managed workspace
- The python3 PATH fallback depends on the correct environment being activated/first on PATH at invocation time; a misconfigured PATH could select a python3 lacking cosalette installed, surfacing a ModuleNotFoundError at server-launch time rather than at ai init time
- Three generation targets (.vscode/mcp.json, .mcp.json, kilo.jsonc) now need to stay behaviorally consistent as the relocatable-command logic evolves, increasing the surface that must be updated together

_2026-08-05_
