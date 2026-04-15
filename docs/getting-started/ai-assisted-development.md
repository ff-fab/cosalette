---
icon: material/robot-outline
---

# AI-Assisted Development

cosalette provides two complementary layers of AI support for downstream app
repositories — a baseline that works in any environment, and an optional MCP
server that gives IDE agents structured, app-aware access to framework
knowledge.

| Layer | What it is | Requirements |
|---|---|---|
| **Baseline** | Compact instruction file + CLI help topics | None — included with `cosalette` |
| **MCP server** | Structured tool surface for IDE-native agents | `cosalette[mcp]` optional extra |

Both layers are bootstrapped from the installed package. There is nothing to
manually copy or download from the hosted documentation.

## Baseline Setup

The baseline works with any AI assistant and has no extra dependencies. It is
the right starting point for all cosalette app repositories.

### Install the instruction file

Run the bootstrap command in your app repository after installing cosalette:

```bash
cosalette ai init    # canonical
cosalette init       # shorthand
```

This installs `.github/instructions/cosalette.instructions.md`. GitHub Copilot
discovers instruction files from `.github/instructions/` automatically — no
editor configuration needed.

cosalette also creates or updates a managed pointer block in `AGENTS.md`. If
`CLAUDE.md` already exists, the same block is updated there. These pointer
files let tools that read top-level agent context files locate the instruction
file — they do not duplicate its content.

### Use CLI help topics

For deeper context on demand:

```bash
cosalette ai prime                  # concise bootstrap overview
cosalette ai help architecture      # design principles and composition patterns
cosalette ai help telemetry         # device registration and publishing
cosalette ai help testing           # unit and integration testing
cosalette ai help configuration     # settings and environment conventions
```

`cosalette prime` is a supported shorthand for `cosalette ai prime`. Topic help
stays under `cosalette ai help <topic>`.

### Refresh after upgrades

After upgrading cosalette, sync the installed instruction file with the new
package version:

```bash
cosalette ai init --force    # overwrite with latest packaged version
cosalette init --force       # shorthand
```

## MCP Server

When `cosalette[mcp]` is installed, `cosalette ai init` also writes
`.vscode/mcp.json`, registering the MCP server with your IDE. IDE agents using
VS Code Copilot, Cursor, Windsurf, or Claude Code then have access to fourteen
structured tools covering framework guidance, app introspection, configuration
schemas, code scaffolding, and ADR context.

### Install

```bash
uv add 'cosalette[mcp]'
# or
pip install 'cosalette[mcp]'
```

### Bootstrap

```bash
cosalette ai init
```

`cosalette ai init` detects `cosalette[mcp]` and writes `.vscode/mcp.json`
automatically alongside the instruction file. No manual server configuration is
needed.

### Start the server

```bash
cosalette ai mcp serve                                  # stdio (IDE default)
cosalette ai mcp serve --transport sse --port 8080      # SSE (team / remote)
python -m cosalette.mcp                                 # alternative stdio entrypoint
```

Your IDE reads `.vscode/mcp.json` and starts the server automatically when
needed. Manual invocation is only required for SSE or non-IDE setups.

For the full tool reference, transport options, and app-aware introspection
setup, see the [MCP Server guide](../guides/mcp-server.md).

For the complete command reference, see
[AI Agent Instructions](../reference/ai-framework-instructions.md).
