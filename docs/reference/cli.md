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
(see [Build a Full App](../guides/full-app.md) for packaging details).

## Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--version` | flag | — | Show application name and version, then exit |
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
```
