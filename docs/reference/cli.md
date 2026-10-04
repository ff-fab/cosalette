---
icon: material/console
---

# CLI Reference

Command-line interface reference for cosalette applications. The CLI is
built automatically by [`App.run()`][cosalette.App] using
[Typer](https://typer.tiangolo.com/), aligning with the framework's
type-hint-driven philosophy (see [ADR-005](../adr/ADR-005-cli-framework.md)).

## Usage

```text
myapp [OPTIONS]
```

The executable name depends on your project's entry point configuration
(see [Build a Full App](../getting-started/full-app.md) for packaging details).

## Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--version` | flag | — | Show application name and version, then exit |
| `--show-devices` | flag | — | Show all registered devices, telemetry, commands, and adapters as a table, then exit |
| `--show-devices-json` | flag | — | Show all registrations as JSON, then exit |
| `--dry-run` | `bool` | `False` | Enable dry-run mode — swaps all registered adapters to their dry-run variants |
| `--log-level` | `str` | *from settings* | Override the log level (see [Log Levels](#log-levels) below) |
| `--log-format` | `str` | *from settings* | Override the log format (see [Log Formats](#log-formats) below) |
| `--env-file` | `str` | `".env"` | Path to the `.env` file used for settings loading |
| `--help` | flag | — | Show the help message and exit |

## Exit Codes

| Code | Constant | Description |
|------|----------|-------------|
| `0` | `EXIT_OK` | Application completed successfully |
| `1` | `EXIT_CONFIG_ERROR` | Configuration validation failed (pydantic `ValidationError`) |
| `3` | `EXIT_RUNTIME_ERROR` | Unhandled exception during the async lifecycle |
| `4` | `EXIT_TASK_FAILURE` | The task supervisor shut the app down: a framework-started task failed under `on_task_failure="exit"`, exhausted its restart budget, or a framework loop died (`TaskSupervisionError`, [ADR-081](../adr/ADR-081-supervision-of-framework-started-tasks-with-an-on-task-failure-policy.md)) |
| `5` | `EXIT_STALE` | A telemetry entity stayed stale for `App(exit_after_stale=...)` seconds (`StaleTelemetryError`, [ADR-083](../adr/ADR-083-opt-in-health-file-and-a-health-cli-probe-for-container-liveness.md)) |

The [`health`](#health-probe) subcommand has its own exit codes: `0` healthy,
`1` unhealthy.

## Log Levels

Valid values for `--log-level` (case-insensitive):

| Value | Description |
|-------|-------------|
| `DEBUG` | Verbose output for development and troubleshooting |
| `INFO` | Normal operational messages (default) |
| `WARNING` | Something unexpected that is not an error |
| `ERROR` | An error occurred but the application continues |
| `CRITICAL` | A severe error — the application may not recover |

## Log Formats

Valid values for `--log-format` (case-insensitive):

| Value | Description |
|-------|-------------|
| `json` | Structured JSON lines for container log aggregators (Loki, Elasticsearch, CloudWatch) — default |
| `text` | Human-readable timestamped lines for local development |

## Example

```bash
# Run with defaults (loads .env, JSON logging at INFO)
myapp

# Development mode: text logs at DEBUG level
myapp --log-level DEBUG --log-format text

# Dry-run with a custom env file
myapp --dry-run --env-file config/staging.env

# Check the version
myapp --version

# Show all registered devices as a table
myapp --show-devices

# Show registrations as JSON (useful for AI agents and scripts)
myapp --show-devices-json
```

## Introspection Flags

The `--show-devices` and `--show-devices-json` flags are **eager** — they
run immediately after argument parsing and exit before settings validation.
This means they work even when the `.env` file is missing or contains
invalid values, making them useful for debugging configuration problems.

`--show-devices` renders a human-readable table sourced from the app's
**AsyncAPI document** (`app.asyncapi()`), grouped by channel archetype
(devices, telemetry, commands). It is **not** the registry snapshot and
carries **no adapters section** — despite the flag name. Empty sections are
omitted. To render the registry snapshot instead — including periodic tasks
and per-entity trigger sources — use
[`cosalette manifest --registry`](#registry-snapshot).
See [Registry Introspection](../concepts/introspection.md) for the snapshot
structure.

`--show-devices-json` outputs the same AsyncAPI data as indented JSON, suitable
for piping into `jq` or consumption by AI coding agents.
When both flags are given, `--show-devices-json` takes precedence.

## Health Probe

`myapp health` and `cosalette health` check the health file that the app writes
when the `COSALETTE_HEALTH_FILE` environment variable names a path
([ADR-083](../adr/ADR-083-opt-in-health-file-and-a-health-cli-probe-for-container-liveness.md)). Use either one as a Docker `HEALTHCHECK` or a Kubernetes exec
probe. Neither one loads settings or connects to the broker.

| Flag | Default | Description |
|------|---------|-------------|
| `--file` | `$COSALETTE_HEALTH_FILE` | Health file to check |
| `--max-age` | 3 x the file's write interval | Seconds after which the file counts as too old, which means the app is hung or dead |
| `--fail-on` | `stale` | Device status that fails the check; repeat the flag to give more than one, for example `--fail-on stale --fail-on error` |

The probe exits `0` and prints the file's age when the file is fresh and no
device has a failing status. It exits `1` and prints the reason on stderr when
the file is missing, unreadable or too old, or when a device has a failing
status. A usage error in the `health` arguments, such as `--max-age -1` or an
unknown option, also exits `1`, with `unhealthy: usage error: <message>` on
stderr, because Docker reserves exit code `2`. See
[Health Checks](../guides/deployment.md#health-checks).

!!! note "Errors before `health` still exit 2"

    Options placed before the subcommand, as in `myapp --bogus health`, are
    parsed by the app's own CLI rather than by `health`, so a usage error there
    exits `2`. Put no options before `health` in a probe command.

## Registry Snapshot

The app-runtime `--show-devices` flags above are AsyncAPI-sourced. To inspect
the **registry snapshot** — the flat view of registrations that also covers
periodic tasks (which have no AsyncAPI channel by construction, ADR-041) and
each entity's `trigger_source` / `min_interval` — use the `cosalette` package
CLI:

```bash
# Registry snapshot as JSON
cosalette manifest myapp.main:app --registry

# Registry snapshot as a human-readable table
cosalette manifest myapp.main:app --registry --table
```

The table adds **Trigger** and **Min interval** columns to the Devices and
Telemetry sections, showing an em-dash for non-triggerable entities. Without
`--registry`, `manifest` and `manifest --table` emit the AsyncAPI document
exactly as before.
