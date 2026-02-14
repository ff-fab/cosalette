# ADR-004: Logging Strategy

## Status

Accepted **Date:** 2026-02-14

## Context

All cosalette applications run as unattended daemons, typically deployed via Docker
containers or systemd services. Logs must be parseable by container log
aggregators (Loki, Elasticsearch, CloudWatch) for centralised monitoring, while
remaining human-readable during local development. The velux2mqtt reference
implementation already includes a custom `JsonFormatter` (105 lines) that emits NDJSON
with UTC timestamps and correlation metadata (`service`, `version`).

Key requirements:

- Container orchestrators (Docker, systemd-journal) parse structured output more
  effectively than free-text
- Cross-timezone deployments require unambiguous timestamps
- Future centralised log aggregation (tooling undecided) requires consistent format
  across all 8+ projects
- Local development needs human-readable output

## Decision

Use **JSON (NDJSON) for production and text for development**, with UTC timestamps and a
parameterised service name, because structured logs are universally parseable by log
aggregators and UTC removes timezone ambiguity across distributed deployments.

The framework provides a `JsonFormatter` that emits one JSON object per log record:

```json
{
  "timestamp": "2026-02-14T12:34:56+00:00",
  "level": "INFO",
  "logger": "velux2mqtt.app",
  "message": "Device blind started",
  "service": "velux2mqtt",
  "version": "0.1.0"
}
```

The format is selectable via configuration (`logging.format = "json" | "text"`) and CLI
(`--log-format`). The `service` field is set from `app.name`, enabling log correlation
across multiple deployed applications.

### Design choices

- **Custom formatter over `python-json-logger`:** Zero additional dependencies. Full
  control over the output schema. Field names match the project's conventions.
- **UTC timestamps (RFC 3339 / ISO 8601):** Container logs cross timezone boundaries.
  UTC removes ambiguity — display layers apply local time when needed.
- **NDJSON format:** Each log line is a complete JSON object with no embedded newlines.
  Critical for container log drivers that split on `\n`.
- **Correlation metadata:** `service` and `version` fields in every log line enable
  filtering by application and version in aggregators without extra configuration.

## Decision Drivers

- The Twelve-Factor App methodology (XI. Logs): treat logs as event streams
- Container log drivers require single-line, structured output
- Cross-timezone portability eliminates local time ambiguity
- Future central log aggregation requires consistent, machine-parseable format
- Development convenience requires human-readable alternative

## Considered Options

### Option 1: structlog

Use the structlog library for structured logging with processors.

- *Advantages:* Rich processor pipeline, context binding, beautiful development output.
- *Disadvantages:* Additional dependency with a significant API surface. The custom
  `JsonFormatter` is only 105 lines and provides exactly what's needed. structlog's
  processor model adds complexity that isn't justified for the use case.

### Option 2: Plain text only

Use Python's default `logging.Formatter` with timestamped text lines.

- *Advantages:* Zero custom code, human-readable by default.
- *Disadvantages:* Not parseable by log aggregators without custom regex patterns.
  No correlation metadata. Breaks with multi-line log messages (exceptions).

### Option 3: Syslog

Use syslog for log routing via the OS.

- *Advantages:* OS-level log management, well-established in server environments.
- *Disadvantages:* Not portable across Docker and bare-metal deployments. Adds
  infrastructure dependency. Not idiomatic for container-based applications.

### Option 4: Dual-format (JSON + text) with custom formatter (chosen)

A custom `JsonFormatter` for production (NDJSON) and standard text formatting for
development, selectable via configuration.

- *Advantages:* Custom formatter is only ~105 lines with zero dependencies.
  NDJSON is universally parseable. UTC timestamps for cross-timezone consistency.
  Parameterised service name enables log correlation. Development mode uses
  readable text.
- *Disadvantages:* Custom code to maintain (albeit small). Must ensure the JSON schema
  remains stable across framework versions.

## Decision Matrix

| Criterion                | structlog | Plain Text | Syslog | Dual-Format Custom |
| ------------------------ | --------- | ---------- | ------ | ------------------ |
| Machine parseability     | 5         | 1          | 3      | 5                  |
| Dev experience           | 5         | 4          | 2      | 4                  |
| Dependency footprint     | 2         | 5          | 4      | 5                  |
| Cross-timezone           | 4         | 2          | 3      | 5                  |
| Aggregator compatibility | 5         | 2          | 3      | 5                  |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- All 8+ projects emit identically structured logs — a single aggregator configuration
  works for the entire fleet
- UTC timestamps eliminate timezone ambiguity across distributed deployments
- `service` and `version` fields enable filtering and grouping without extra
  log pipeline configuration
- Developers can switch to human-readable text output with `--log-format text`
- Zero additional dependencies — the formatter uses only Python stdlib (`json`,
  `logging`, `datetime`)

### Negative

- Custom `JsonFormatter` is project-maintained code (~105 lines) rather than a
  community-maintained library
- The JSON schema becomes a contract — field names and structure must remain stable
  to avoid breaking log pipeline configurations
- Text mode output does not include correlation metadata (`service`, `version`),
  reducing its usefulness in production

_2026-02-14_
