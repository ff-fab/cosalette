---
icon: material/transfer
---

# Migrate a Legacy IoT App with AI Agents

This guide shows how to use cosalette's AI development support to migrate an
existing IoT bridge application to the framework — either as a refactor of the
existing codebase, or as analysis input for a new cosalette project built from
scratch.

!!! note "Prerequisites"

    Install cosalette in your target app repository before starting:

    ```bash
    uv add cosalette
    # or: pip install cosalette

    # Optional — adds MCP tools for IDE-native agents
    uv add 'cosalette[mcp]'
    ```

## Bootstrap the AI layer

Run the bootstrap command in your app repository (not the cosalette source):

```bash
cosalette ai init
```

This installs `.github/instructions/cosalette.instructions.md` and registers
the MCP server if `cosalette[mcp]` is present. GitHub Copilot and Claude Code
discover the instruction file automatically — no editor configuration needed.

Give the agent a project-wide orientation before starting migration work:

```bash
cosalette ai prime       # concise bootstrap overview
cosalette ai help architecture   # composition and hexagonal patterns
cosalette ai help telemetry      # device registration patterns
cosalette ai help configuration  # Settings extension and env conventions
cosalette ai help testing        # AppHarness, MockMqttClient, FakeClock
```

With the MCP server active, `cosalette_scaffold` and `cosalette_telemetry_guide`
give agents structured scaffolding on demand without requiring explicit `help`
invocations.

---

## Route A — Refactor an existing project

Use this route when the existing codebase will become the cosalette app.

### Step 1: Analyse the legacy app

Ask your agent to analyse the existing codebase against the following
dimensions. The output drives the planning step.

```text
Analyse this codebase as a cosalette migration candidate. For each dimension
below, describe what the legacy code does and which cosalette pattern it maps to.
Use `cosalette ai help architecture` and `cosalette ai help telemetry` for
pattern context.

Dimensions:
a. Device inventory — what entities publish or receive MQTT messages?
   Note whether device count is static or could be settings-driven.
   Dynamic counts map to @app.on_configure for registration.

b. Telemetry loops — identify while-True polling loops and their intervals.
   Note whether intervals are fixed or read from config.

c. Command handling — how are incoming MQTT messages received and dispatched?
   Note payload inspection patterns, sub-topic routing, and asyncio.Queue
   bridges between listeners and device loops.

d. Hardware adapters — which classes wrap physical hardware or external APIs?
   Note whether they can enter wedged states (BLE, serial, GPIO). Those map
   to the HealthCheckable protocol for auto-restart.

e. Error handling — categorise exceptions as transient (retry later) or
   permanent (stop device). Transient failures in telemetry handlers can use
   the built-in retry= parameter with retry_on= exception types.

f. Shared state — identify module-level globals and mutable state shared
   across handlers. These should be yielded from the lifespan hook for DI
   injection rather than using globals.

g. Lifecycle — identify startup/shutdown logic (hardware init, connection
   setup, cleanup). Map to @app.on_configure or the lifespan context manager.

h. Sub-entities — identify devices with sub-components that have independent
   availability (e.g., calibration mode, diagnostic mode). These map to
   ctx.sub_entity() context managers.

i. Scheduling — is data acquisition time-of-day aligned (calendar events,
   daily meter readings)? If so, ctx.sleep_until() replaces fixed-interval
   polling.
```

### Step 2: Map legacy patterns to cosalette patterns

Use the analysis output to produce a pattern mapping. The agent can produce
this as a migration plan document.

| Legacy pattern | cosalette pattern |
|---|---|
| `while True: await asyncio.sleep(n)` polling loop | `@app.telemetry("name", interval=n)` |
| `asyncio.Queue` bridging MQTT listener to device loop | `ctx.commands()` async iterator |
| Payload `json.loads` + `if data["action"] == ...` routing | `@ctx.on_command("sub_topic")` per sub-topic |
| Eager `_settings = Settings()` for dynamic device list | `@app.on_configure` with dict-name decorators |
| Module-level global for shared connection pool / registry | Yield from `lifespan` hook → injected via DI |
| `try/except` with bare `continue` on transient failures | `retry=3, retry_on=(SensorTimeout,)` on `@app.telemetry` |
| Hardware class with no health surface | Implement `HealthCheckable` protocol |
| `asyncio.sleep` on calendar-aligned schedule | `ctx.sleep_until(next_aligned_time)` |
| Sub-component with independent up/down state | `async with ctx.sub_entity("name"):` |
| Direct `paho.Client.publish()` calls | Return `dict` from handler — framework publishes |

A complete prompt for the planning step:

```text
Using the analysis above, produce a cosalette migration plan for this app.
For each identified device, show:
  1. The decorator to use (@app.telemetry / @app.command / @app.device)
  2. Any init= factory needed for DI
  3. Which legacy module becomes ports.py, adapters.py, settings.py, app.py
  4. Error handling: which exceptions are transient (retry=) vs permanent
  5. Whether @app.on_configure is needed for dynamic registration

Scaffold the app.py shell using the cosalette_scaffold MCP tool, then fill
in the device registrations from the plan.
```

### Step 3: Implement incrementally

Migrate one device at a time. A stable pattern is:

1. **Extract the port** — define a `Protocol` in `ports.py` for each hardware
   dependency (see the [Adapters guide](adapters.md)).
2. **Scaffold the registration** — use the `cosalette_scaffold` MCP tool or
   ask the agent to produce the decorator shell.
3. **Move handler logic** — copy the loop body into the handler function,
   removing the `while True` and `await asyncio.sleep`.
4. **Wire DI** — replace global state accesses with `init=` factories or
   `ctx.adapter(PortType)`.
5. **Add tests** — use `AppHarness` for integration coverage
   (see the [Testing guide](testing.md)).

Repeat until all legacy devices are registered. The legacy `main()` becomes
`app.run()`.

---

## Route B — New project from legacy analysis

Use this route when the legacy codebase is a reference for a new,
clean-slate cosalette project.

### Step 1: Inventory the legacy app

Provide the legacy source to your agent and ask it to extract a structured
inventory — you do not need to refactor the existing files:

```text
Analyse this legacy IoT app as a specification for a new cosalette project.
Do not modify any files. Extract:

1. A list of MQTT entities (topic → data shape)
2. A list of commands (topic → expected payload shape)
3. Configuration values that should become Settings fields
4. Hardware dependencies that need Protocol ports
5. Any shared runtime state that should come from the lifespan hook
6. Scheduling patterns (fixed interval vs time-of-day aligned)

Output in structured form so it can be used as scaffolding input.
```

### Step 2: Scaffold the new project

With the inventory in hand, ask the agent to scaffold the project:

```text
Using the inventory above and the cosalette_scaffold MCP tool, scaffold a new
cosalette project called <name> with:
- Settings class with the identified configuration fields
- @app.telemetry / @app.command registrations for each MQTT entity
- ports.py with Protocol definitions for each hardware dependency
- A lifespan hook yielding shared state where needed
- Skeleton tests using AppHarness for each device
```

If `cosalette[mcp]` is installed, the `cosalette_scaffold` tool generates
idiomatic, lint-clean stubs directly. Without MCP, use:

```bash
cosalette ai help telemetry
cosalette ai help configuration
```

to prime the agent before asking it to write the registration code manually.

### Step 3: Implement the adapters

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

After completing either route, verify with the standard quality gate:

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
