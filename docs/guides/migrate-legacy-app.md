---
icon: material/transfer
---

# Migrate a Legacy IoT App with AI Agents

This guide shows how to use cosalette's AI development support to build a new,
clean-slate cosalette project from an existing IoT bridge application used as
a specification.

!!! note "Prerequisites"

    Install cosalette in your **new** app repository before starting:

    ```bash
    uv add cosalette
    # or: pip install cosalette

    # Optional — adds MCP tools for IDE-native agents
    uv add 'cosalette[mcp]'
    ```

## Bootstrap the AI layer

Run the bootstrap command in your new app repository:

```bash
cosalette ai init
```

This installs `.github/instructions/cosalette.instructions.md` and registers
the MCP server if `cosalette[mcp]` is present. GitHub Copilot and Claude Code
discover the instruction file automatically — no editor configuration needed.

---

## Step 1: Derive the legacy app's purpose and behaviour

Point your agent at the legacy source and ask it to describe what the
application does — without yet thinking about cosalette. This gives you a
specification to work from and surfaces implicit requirements that a pure
inventory pass would miss:

```text
Read this codebase and describe the application:
- What is its purpose and what problem does it solve?
- What physical devices or external services does it interact with?
- What data does it publish, and to where?
- What commands or external inputs does it respond to?
- What are its operational characteristics (polling frequency, reliability
  requirements, error behaviour)?
- Are there any non-obvious behaviours, quirks, or workarounds in the code
  that a reimplementation must preserve?

Do not suggest any changes. Just describe what the app does.
Write the output to docs/planning/legacy-app-description.md.
```

### Step 2: Inventory the legacy app

With the description in hand, provide the legacy source to your agent and ask
it to extract a structured inventory — you do not need to refactor the
existing files:

```text
Read docs/planning/legacy-app-description.md for context, then analyse the
legacy source as a specification for a new cosalette project.
Do not modify any files. Extract:

1. A list of MQTT entities (topic → data shape)
2. A list of commands (topic → expected payload shape)
3. Configuration values that should become Settings fields
4. Hardware dependencies that need Protocol ports
5. Any shared runtime state that should come from the lifespan hook
6. Scheduling patterns (fixed interval vs time-of-day aligned)

Write the output to docs/planning/legacy-app-inventory.md in structured
Markdown so it can be used as scaffolding input.
```

### Step 3: Scaffold the new project

With both planning documents in place, ask the agent to scaffold the project:

```text
Read docs/planning/legacy-app-description.md and
docs/planning/legacy-app-inventory.md, then use the cosalette_scaffold MCP
tool to scaffold a new cosalette project called <name> with:
- Settings class with the configuration fields from the inventory
- @app.telemetry / @app.command registrations for each MQTT entity
- ports.py with Protocol definitions for each hardware dependency
- A lifespan hook yielding shared state where needed
- Skeleton tests using AppHarness for each device

Preserve the behaviours and quirks noted in the description.
```

If `cosalette[mcp]` is installed, the `cosalette_scaffold` tool generates
idiomatic, lint-clean stubs directly. Without MCP, use:

```bash
cosalette ai help telemetry
cosalette ai help configuration
```

to prime the agent before asking it to write the registration code manually.

### Step 4: Implement the adapters

Port the hardware interaction code from the legacy app into the new adapters.
The scaffolded `ports.py` defines the interfaces; the agent can fill in
concrete adapter classes:

```text
Implement the adapters in adapters.py for the ports defined in ports.py.
Port the hardware interaction logic from <legacy_file> into each adapter.
Each adapter must satisfy its protocol structurally (no inheritance needed).
Register them with app.adapter() in app.py.
```

---

## Verifying the migration

Verify with the standard quality gate:

```bash
task check         # lint + typecheck + tests
task test:cov      # coverage report
```

Ask the agent to check architectural conformance:

```text
Review app.py against cosalette conventions:
- No module-level globals that should be in lifespan or DI
- No bare asyncio.sleep — use ctx.sleep() so shutdown is respected
- No direct MQTT publish calls — handlers return dicts
- All hardware dependencies behind Protocol ports

Use `cosalette ai help architecture` for the full checklist.
```

---

## What the agent cannot infer

Some migration decisions require human judgment:

- **MQTT topic hierarchy** — cosalette uses `<app>/<device>/state` and
  `<app>/<device>/set` conventions. If the legacy app uses a different
  scheme, you may need to configure or override topics explicitly.
- **Transient vs permanent exceptions** — the agent can identify exception
  types but cannot know whether a given exception class represents a
  recoverable condition in your hardware environment.
- **Interval selection** — existing sleep durations are a starting point;
  review them in the context of broker load and downstream consumer latency
  requirements.
