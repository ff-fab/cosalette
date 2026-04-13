---
icon: material/robot-outline
---

# AI-Assisted Development

If you use GitHub Copilot or another coding agent in a cosalette app
repository, bootstrap framework guidance from the installed cosalette package.

This page targets the normal downstream setup: you already ran `uv add
cosalette` or `pip install cosalette` in your own repository.

## 1. Install the Packaged Instruction File

Run the canonical bootstrap command in your app repository:

```bash
cosalette ai init
```

Supported shorthand:

```bash
cosalette init
```

The current supported workflow installs this file:

```text
.github/instructions/cosalette.instructions.md
```

That file is packaged with cosalette itself and copied into your repository by
the CLI. It is the single canonical source of framework guidance. You do not
need to manually copy or download an instructions file from the hosted
documentation.

On canonical installs and refreshes, cosalette also creates or updates a
managed pointer block in `AGENTS.md`. If `CLAUDE.md` already exists, cosalette
updates the same pointer block there too.

## 2. Let Copilot Discover It

GitHub Copilot automatically reads instruction files from
`.github/instructions/`. After `cosalette ai init`, your repository has the
canonical cosalette framework guidance in the location Copilot already knows how
to discover.

`AGENTS.md` and `CLAUDE.md` are compatibility shims for tools that consult
those files. Their managed blocks point back to
`.github/instructions/cosalette.instructions.md` instead of duplicating the
guidance. Whether a specific tool reads those files still depends on that tool.

The installed file is intentionally compact. It covers framework conventions,
common patterns, and failure-prone details without trying to embed the full
framework reference into every AI interaction.

## 3. Pull Deeper Framework Context On Demand

When you want more than the compact instruction file, use the local CLI help
surface that ships with the installed package:

```bash
cosalette ai prime
cosalette prime
cosalette ai help architecture
cosalette ai help telemetry
cosalette ai help testing
cosalette ai help configuration
```

Use these commands for deeper local framework context:

- `cosalette ai prime` or `cosalette prime` for a concise bootstrap summary
- `cosalette ai help architecture` for design principles and composition patterns
- `cosalette ai help telemetry` for device registration and publishing patterns
- `cosalette ai help testing` for unit and integration testing guidance
- `cosalette ai help configuration` for settings and environment conventions

Only `init` and `prime` have top-level shorthand aliases today. Topic help stays
under `cosalette ai help <topic>`.

## 4. Refresh After Upgrades

After you upgrade cosalette, refresh the installed instructions file so it stays
aligned with the version in your environment:

```bash
cosalette ai init --force
```

Supported shorthand:

```bash
cosalette init --force
```

This overwrites `.github/instructions/cosalette.instructions.md` with the latest
packaged version from the installed cosalette release. On canonical refreshes,
cosalette also refreshes the managed pointer block in `AGENTS.md` and in
`CLAUDE.md` when that file already exists.

## 5. Know the Current Scope

The supported bootstrap model is the canonical instruction file plus local CLI
help topics.

- `.github/instructions/cosalette.instructions.md` is the single canonical source
- GitHub Copilot can discover `.github/instructions/` directly
- canonical installs create or update a managed pointer block in `AGENTS.md`
- canonical installs update `CLAUDE.md` only when that file already exists
- custom `--target` installs skip `AGENTS.md` / `CLAUDE.md` auto-management
- deeper framework rationale lives in `cosalette prime` and `cosalette ai help <topic>`

If you use `--target` to install the guidance somewhere else, maintain any
compatibility pointer files yourself.

For the reference version of this workflow, see
[AI Agent Instructions](../reference/ai-framework-instructions.md).
