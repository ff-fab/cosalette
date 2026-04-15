# cosalette

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/images/brand/hero-banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/images/brand/hero-banner-light.png">
  <img alt="cosalette — An opinionated Python framework for IoT-to-MQTT bridges" src="docs/assets/images/brand/hero-banner-dark.png" style="max-width: 100%; height: auto;">
</picture>

[![CI](https://github.com/ff-fab/cosalette/actions/workflows/ci.yml/badge.svg)](https://github.com/ff-fab/cosalette/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/ff-fab/cosalette/graph/badge.svg)](https://codecov.io/gh/ff-fab/cosalette)
[![Tests](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/ff-fab/18bf35c516091db0ca767ebf497f2b8f/raw/test-badge.json)](https://gist.githack.com/ff-fab/18bf35c516091db0ca767ebf497f2b8f/raw/test-report.html)
[![Docs](https://github.com/ff-fab/cosalette/actions/workflows/docs.yml/badge.svg)](https://ff-fab.github.io/cosalette/)
[![PyPI](https://img.shields.io/pypi/v/cosalette)](https://pypi.org/project/cosalette/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.14-blue)](https://www.python.org/)

**[Documentation](https://ff-fab.github.io/cosalette/)** ·
**[Dev Docs](https://cosalette-main.surge.sh)** ·
**[Quickstart](https://ff-fab.github.io/cosalette/getting-started/quickstart/)** ·
**[API Reference](https://ff-fab.github.io/cosalette/reference/api/)**

---

## What is cosalette?

cosalette lets you build IoT-to-MQTT bridge daemons in Python with minimal boilerplate.
You define **devices** (telemetry pollers or command handlers), register **hardware
adapters**, and the framework handles MQTT wiring, structured logging, health reporting,
error publishing, and graceful lifecycle management.

### Key Features

- **Declarative device registration** — `@app.device()`, `@app.telemetry()`, and
  `@app.command()` decorators with imperative equivalents
  ([guide](https://ff-fab.github.io/cosalette/guides/command-device/))
- **Hexagonal architecture** — protocol-based ports with swappable adapters
  ([concept](https://ff-fab.github.io/cosalette/concepts/hexagonal/))
- **Publish strategies** — control when telemetry is published with `OnChange`, `Every`,
  and composable `All`/`Any` strategies
  ([concept](https://ff-fab.github.io/cosalette/concepts/publish-strategies/))
- **Command sub-topic routing** — `@ctx.on_command("calibrate")` handlers and
  `async for cmd in ctx.commands()` iterator pattern
  ([guide](https://ff-fab.github.io/cosalette/guides/command-device/))
- **Cron & interval scheduling** — `interval=` seconds, `schedule=` Quartz cron
  expressions, and `ctx.sleep_until()` for wall-clock timing
- **Signal filters** — Rust-backed `Pt1Filter`, `MedianFilter`, and `OneEuroFilter` for
  real-time noise reduction
  ([concept](https://ff-fab.github.io/cosalette/concepts/signal-filters/))
- **Persistence** — `Store` protocol with JSON, SQLite, and in-memory backends; save
  policies like `SaveOnChange` and `SaveOnPublish`
  ([concept](https://ff-fab.github.io/cosalette/concepts/persistence/))
- **Health checks & auto-restart** — `HealthCheckable` protocol for adapters, periodic
  health monitoring, LWT crash detection, and automatic adapter restart on failure
  ([concept](https://ff-fab.github.io/cosalette/concepts/health-reporting/))
- **Retry & backoff** — configurable retry with `ExponentialBackoff`, `LinearBackoff`,
  `FixedBackoff`, and `CircuitBreaker` per telemetry device
- **Schema enforcement** — validate MQTT payloads against AsyncAPI 3.0.0 schemas at
  publish-time with `warn` or `block` modes
  ([guide](https://ff-fab.github.io/cosalette/guides/schema-enforcement/))
- **Lifespan & dependency injection** — `lifespan=` async context manager for
  startup/teardown, type-based DI for adapters, settings, and yielded state
  ([guide](https://ff-fab.github.io/cosalette/guides/lifespan/))
- **Structured JSON logging** — per-device context, configurable levels
  ([concept](https://ff-fab.github.io/cosalette/concepts/logging/))
- **Structured error publishing** — domain errors published to MQTT with type mapping
  ([concept](https://ff-fab.github.io/cosalette/concepts/error-handling/))
- **Pydantic settings** — type-safe configuration from env vars and `.env` files
  ([guide](https://ff-fab.github.io/cosalette/guides/configuration/))
- **CLI for free** — `--dry-run`, `--version`, `--log-level`, `--show-devices` via Typer
  ([reference](https://ff-fab.github.io/cosalette/reference/cli/))
- **MCP server** — optional `cosalette[mcp]` extra for IDE-native AI agent integration
  ([guide](https://ff-fab.github.io/cosalette/guides/mcp-server/))
- **Test-friendly** — `AppHarness`, `MockMqttClient`, `FakeClock`, and pytest fixtures
  included ([guide](https://ff-fab.github.io/cosalette/guides/testing/))

## Quick Example

```python
import cosalette

app = cosalette.App(name="sensor2mqtt", version="0.1.0")

@app.telemetry("sensor", interval=5.0)
async def sensor() -> dict[str, object]:
    return {"temperature": 21.5, "humidity": 55.0}

if __name__ == "__main__":
    app.run()
```

See the full
[Quickstart](https://ff-fab.github.io/cosalette/getting-started/quickstart/) for a
complete walkthrough.

## Installation

```bash
pip install cosalette
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add cosalette
```

To include the optional MCP server for IDE-native AI agent integration (VS Code Copilot,
Cursor, Windsurf, Claude Code):

```bash
uv add 'cosalette[mcp]'
pip install 'cosalette[mcp]'
```

See the [MCP Server guide](https://ff-fab.github.io/cosalette/guides/mcp-server/) for
setup and tool reference.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, commands, project
structure, and code quality standards.

## Documentation

Full documentation is published at
**[ff-fab.github.io/cosalette](https://ff-fab.github.io/cosalette/)**. Development docs
tracking the `main` branch are available at
**[cosalette-main.surge.sh](https://cosalette-main.surge.sh)**.

| Section                                                                | What you'll find                                                 |
| ---------------------------------------------------------------------- | ---------------------------------------------------------------- |
| [Getting Started](https://ff-fab.github.io/cosalette/getting-started/) | Installation, quickstart tutorial                                |
| [Concepts](https://ff-fab.github.io/cosalette/concepts/)               | Architecture, lifecycle, health checks, filters, persistence     |
| [How-To Guides](https://ff-fab.github.io/cosalette/guides/)            | Telemetry, commands, adapters, lifespan DI, schema, testing, MCP |
| [Reference](https://ff-fab.github.io/cosalette/reference/)             | API docs, CLI options, payload schemas                           |
| [ADRs](https://ff-fab.github.io/cosalette/adr/)                        | Architecture Decision Records                                    |

## License

MIT License. See [LICENSE](LICENSE) for details.
