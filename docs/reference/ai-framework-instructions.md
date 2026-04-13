---
icon: material/file-document-edit
---

# AI Agent Instructions

cosalette supports AI-friendly downstream development through the installed
package CLI. After you install cosalette in your app repository, use the CLI to
install or refresh the packaged instruction file locally.

The supported workflow is **package CLI bootstrap**, not manual copying or
downloading from the hosted documentation.

## Supported Bootstrap Flow

After `uv add cosalette` or `pip install cosalette`, run:

```bash
cosalette ai init
```

Supported shorthand:

```bash
cosalette init
```

`cosalette ai init` is the canonical command. Its default target for installs
and refreshes is:

```text
.github/instructions/cosalette.instructions.md
```

That file is the single canonical source of framework guidance. If
`.github/instructions/` does not exist yet, the CLI creates it for you.
GitHub Copilot can discover instruction files in `.github/instructions/`
directly, so the default path works without extra editor configuration.

## What Gets Installed

The installed file is a compact framework guide that ships inside the installed
cosalette package. It focuses on the things AI assistants most often need in a
consuming app repository:

- The `App` composition-root pattern
- Declarative registration with `@app.telemetry()`, `@app.command()`, and `@app.device()`
- Type-based dependency injection via `init=`
- Settings, lifecycle, testing, and error-handling patterns
- Pointers to local CLI help for deeper framework context

The shipped template currently applies to Python files via:

```yaml
applyTo: '**/*.py'
```

That compact scope is intentional. Deeper rationale and examples live behind
local CLI help commands instead of a very large static instructions file.

## Compatibility Pointer Files

On canonical installs and refreshes, cosalette also manages compatibility
pointer blocks for tools that consult top-level agent files.

- `AGENTS.md`: cosalette creates the file if needed and creates or updates a
  managed pointer block
- `CLAUDE.md`: cosalette updates the same managed pointer block only if the file
  already exists

These files point back to `.github/instructions/cosalette.instructions.md`
instead of duplicating the framework guidance. Whether a tool reads
`.github/instructions/`, `AGENTS.md`, or `CLAUDE.md` still depends on that
tool.

## Custom Target Paths

If you pass `--target`, cosalette writes the instruction file to that path and
skips `AGENTS.md` / `CLAUDE.md` auto-management. Use that mode only when your
toolchain needs a non-canonical location, and manage any compatibility pointer
files yourself.

## Refresh After Upgrading cosalette

When you upgrade cosalette, refresh the local instruction file from the newly
installed package version:

```bash
cosalette ai init --force
```

Supported shorthand:

```bash
cosalette init --force
```

`--force` overwrites the existing
`.github/instructions/cosalette.instructions.md` file with the latest packaged
template. On canonical refreshes, cosalette also refreshes the managed pointer
blocks described above when applicable. If you added repo-specific edits to that
file, review and reapply them after refreshing.

## Get Deeper Local Framework Context

Use the installed CLI for deeper framework context when you need it:

```bash
cosalette ai prime
cosalette prime
cosalette ai help architecture
cosalette ai help telemetry
cosalette ai help testing
cosalette ai help configuration
```

`cosalette ai prime` is the canonical form and `cosalette prime` is the supported
shorthand alias. Topic help remains namespaced under `cosalette ai help <topic>`.

The current packaged help topics are:

- `architecture`
- `telemetry`
- `testing`
- `configuration`

## Current Scope

The downstream bootstrap model is:

- `.github/instructions/cosalette.instructions.md` is the single canonical source
- `cosalette ai init` and `cosalette init` target that canonical file by default
- canonical installs also manage a pointer block in `AGENTS.md` and, if the file exists, `CLAUDE.md`
- custom `--target` installs skip `AGENTS.md` / `CLAUDE.md` auto-management
- GitHub Copilot can discover `.github/instructions/` directly
- other tools may use the pointer files, but discovery still depends on the tool
