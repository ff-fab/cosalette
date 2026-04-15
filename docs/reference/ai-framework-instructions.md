---
icon: material/file-document-edit
---

# AI Agent Instructions

Reference for the `cosalette` package CLI commands that manage AI support in
downstream app repositories. For a task-oriented walkthrough, see
[AI-Assisted Development](../getting-started/ai-assisted-development.md).

## `cosalette ai init`

Installs or refreshes the packaged instruction file in the current repository.

```bash
cosalette ai init              # install
cosalette ai init --force      # overwrite existing file
cosalette ai init --target PATH  # install to a custom path
cosalette init                 # shorthand; supports the same flags
cosalette init --force         # shorthand
```

**Default target:** `.github/instructions/cosalette.instructions.md`

GitHub Copilot discovers instruction files from `.github/instructions/`
automatically. The default path requires no editor configuration.

**What gets installed:** A compact framework guide covering:

- `App` composition-root pattern
- `@app.telemetry()`, `@app.command()`, `@app.device()` declarative registration
- Type-based dependency injection via `init=`
- Settings, lifecycle, testing, and error-handling patterns
- Pointers to local CLI help topics

**Compatibility pointer files:** Canonical installs (without `--target`) also
manage these files:

- `AGENTS.md` — created if absent; managed pointer block created or updated
- `CLAUDE.md` — managed pointer block updated only if the file already exists

The pointer blocks reference `.github/instructions/cosalette.instructions.md`
rather than duplicating its content. Custom `--target` installs skip this
management entirely.

**MCP side-effect:** When `cosalette[mcp]` is installed, `cosalette ai init`
also writes `.vscode/mcp.json` registering the MCP server with the IDE. See
[MCP server commands](#mcp-server-commands) below.

## `cosalette ai prime`

Prints a concise bootstrap summary for downstream agents and developers.

```bash
cosalette ai prime    # canonical
cosalette prime       # shorthand
```

## `cosalette ai help`

Prints curated framework guidance on a specific topic.

```bash
cosalette ai help architecture      # design principles and composition patterns
cosalette ai help telemetry         # device registration and publishing
cosalette ai help testing           # unit and integration testing
cosalette ai help configuration     # settings and environment conventions
```

Topic help is namespaced under `cosalette ai help <topic>`. There is no
top-level shorthand for topic help.

## MCP Server Commands

Available when `cosalette[mcp]` (`uv add 'cosalette[mcp]'`) is installed.

### `cosalette ai mcp serve`

Starts the cosalette MCP server.

```bash
cosalette ai mcp serve                                  # stdio transport (default)
cosalette ai mcp serve --transport sse --port 8080      # SSE transport
python -m cosalette.mcp                                 # alternative stdio entrypoint
```

| Option | Default | Description |
|---|---|---|
| `--transport` / `-t` | `stdio` | Transport type: `stdio` or `sse` |
| `--port` / `-p` | `8080` | Port number for SSE transport |

**stdio** is the correct transport for IDE integration (VS Code Copilot, Cursor,
Windsurf, Claude Code). In normal IDE workflows the IDE starts the server
automatically via `.vscode/mcp.json` — manual invocation is only needed for SSE
or non-IDE setups.

### `.vscode/mcp.json`

Written by `cosalette ai init` when `cosalette[mcp]` is installed:

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

If the file already exists, cosalette merges the `cosalette` server entry rather
than overwriting the file.

For the full MCP tool reference, see the [MCP Server guide](../guides/mcp-server.md).
