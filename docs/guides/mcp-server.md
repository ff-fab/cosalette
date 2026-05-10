---
icon: material/robot-outline
---

# MCP Server

The cosalette MCP server exposes fourteen structured tools for IDE-native AI
agents. It is an optional layer on top of the baseline instruction file and CLI
help — install it when you want agents to query app-specific registrations,
generate idiomatic scaffolding, or look up architectural decisions
programmatically.

This guide covers installation, bootstrap, local transport, and the full tool
reference.

## Prerequisites

- cosalette installed in your app repository (`uv add cosalette`)
- An IDE with MCP support (VS Code Copilot, Cursor, Windsurf, Claude Code)

## Installation

```bash
uv add 'cosalette[mcp]'
# or
pip install 'cosalette[mcp]'
```

This adds `fastmcp` as a dependency alongside cosalette.

## Bootstrap

```bash
cosalette ai init    # canonical
cosalette init       # shorthand
```

When `cosalette[mcp]` is present, `cosalette ai init` writes `.vscode/mcp.json`
in addition to the standard instruction file:

```json
{
  "servers": {
    "cosalette": {
      "command": "cosalette",
      "args": ["ai", "mcp", "serve"],
      "env": {}
    }
  }
}
```

If `.vscode/mcp.json` already exists, cosalette merges the `cosalette` entry
rather than overwriting the file. Your IDE reads this file and starts the server
automatically when an agent needs it.

## Transport

cosalette MCP uses **stdio** only. This is the transport used by IDE integrations
such as VS Code Copilot, Cursor, Windsurf, and Claude Code. The bootstrap config
sets stdio automatically, so you do not need to start the server manually in
normal IDE workflows.

```bash
cosalette ai mcp serve                                  # stdio (IDE default)
python -m cosalette.mcp                                 # alternative stdio entrypoint
```

!!! note "Why SSE is not exposed"

    Introspection and configuration tools dynamically import caller-supplied
    modules (`app_spec`, `settings_spec`). Under stdio the IDE starts the server
    locally and passes requests over the process boundary. Exposing the same
    tools through a network listener would enlarge the trust boundary without a
    current cosalette use case, so `--transport sse` fails closed.

## Tool Reference

The server provides fourteen tools in five feature groups.

### F1 — Framework Guidance

Static tools — no app context required. Agents use these to query framework
conventions and help topics without importing your application.

| Tool | Description |
|---|---|
| `cosalette_prime` | Bootstrap overview — same content as `cosalette ai prime` |
| `cosalette_conventions` | Compact conventions and constraints summary |
| `cosalette_help` | Topic guidance: `architecture`, `telemetry`, `testing`, `configuration` |

### F2 — App Introspection

Runtime tools that import your application module to inspect its registrations.
All require an `app_spec` argument.

| Tool | Description |
|---|---|
| `cosalette_inspect_app` | Full registry snapshot — devices, telemetry, commands, adapters, metadata |
| `cosalette_inspect_device` | Details for a named device |
| `cosalette_inspect_adapters` | All adapter registrations and port mappings |
| `cosalette_manifest` | Canonical AsyncAPI 3.0.0 contract via `app.asyncapi()` — typed payload schemas, operations, `x-cosalette-contract-version` metadata |

**`app_spec` format:** `"module.path:attribute"` — for example `"myapp.main:app"`
or `"myapp:app"`. The module path is relative to the working directory where the
IDE started (normally the repository root).

Hardware dependencies that are unavailable in the development environment are
caught at import time — the introspection tools return a partial snapshot with
an error note rather than crashing. Use the dry-run adapter pattern to stub
hardware in development (see [Hardware Adapters](adapters.md)).

### F3 — Configuration Schema

Runtime tools that import a Settings class to expose its schema and environment
variable inventory. Both accept an optional `settings_spec` argument in the same
`"module.path:attribute"` format. When omitted, they use the base `Settings`
class.

| Tool | Description |
|---|---|
| `cosalette_config_schema` | Full JSON Schema for the Settings class |
| `cosalette_config_env_vars` | Environment variable names, types, and defaults |

Secret-looking fields (names containing `password`, `token`, `secret`, or `key`)
have their default values redacted in the env-var listing.

### F4 — Code Scaffolding

Code generation tools that produce idiomatic, ready-to-run Python source. When
`app_spec` is provided, scaffolding checks for name collisions against existing
registrations and can suggest registered adapter ports.

| Tool | Key parameters | Output |
|---|---|---|
| `cosalette_scaffold_device` | `device_name`, `adapter_port` (optional), `adapter_module` (optional), `app_spec` (optional) | Telemetry device handler with optional adapter injection |
| `cosalette_scaffold_adapter` | `port_name`, `device_description`, `return_type`, `default_value` | Port `Protocol` + concrete adapter + dry-run/fake adapter |
| `cosalette_scaffold_test` | `device_name`, `func_name`, `adapter_port` (optional), `app_spec` (optional) | Unit tests + `AppHarness` integration tests |

Copy the generated source into your project and adjust imports as needed. The
output follows the same patterns described in the [Telemetry Device](telemetry-device.md)
and [Hardware Adapters](adapters.md) guides.

### F5 — ADR Context

Read-only tools that query the packaged ADR index. No app context required.
The index is bundled in the installed wheel and covers all architectural
decisions made for cosalette.

| Tool | Description |
|---|---|
| `cosalette_list_adrs` | Index of all ADRs with status and one-line summary |
| `cosalette_get_adr` | Full content of a specific ADR by ID (e.g. `ADR-035`) |
| `cosalette_search_adrs` | Keyword search across ADR titles, tags, summaries, and content |

## Refresh After Upgrades

After upgrading cosalette, re-run `cosalette ai init --force` to refresh both
the instruction file and `.vscode/mcp.json`:

```bash
cosalette ai init --force
cosalette init --force       # shorthand
```

The server binary path rarely changes between versions, but `--force` keeps
`.vscode/mcp.json` consistent.

## Troubleshooting

**`cosalette ai mcp serve` reports MCP not installed**

The `[mcp]` extra is missing. Run `uv add 'cosalette[mcp]'`.

**Introspection tools cannot import `app_spec`**

Verify that the dotted module path is correct relative to the repository root
(e.g. `"myapp.main:app"`, not `"./myapp/main.py:app"`). The server runs in the
directory where the IDE process started, which is normally the repository root.

**Hardware imports fail during introspection**

See the note under [F2 — App Introspection](#f2-app-introspection). Stub
hardware using the dry-run adapter pattern
([Hardware Adapters guide](adapters.md)).
