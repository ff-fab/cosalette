# cosalette

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ff-fab/cosalette/main/docs/assets/images/brand/hero-banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/ff-fab/cosalette/main/docs/assets/images/brand/hero-banner-light.png">
  <img alt="cosalette — An opinionated Python framework for IoT-to-MQTT bridges" src="https://raw.githubusercontent.com/ff-fab/cosalette/main/docs/assets/images/brand/hero-banner-dark.png" style="max-width: 100%; height: auto;">
</picture>

<div align="center">

[![CI](https://github.com/ff-fab/cosalette/actions/workflows/ci.yml/badge.svg)](https://github.com/ff-fab/cosalette/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/ff-fab/cosalette/graph/badge.svg)](https://codecov.io/gh/ff-fab/cosalette)
[![Tests](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/ff-fab/18bf35c516091db0ca767ebf497f2b8f/raw/test-badge.json)](https://gist.githack.com/ff-fab/18bf35c516091db0ca767ebf497f2b8f/raw/test-report.html)
[![Docs](https://github.com/ff-fab/cosalette/actions/workflows/docs.yml/badge.svg)](https://ff-fab.github.io/cosalette/)
[![PyPI](https://img.shields.io/pypi/v/cosalette?color=blue)](https://pypi.org/project/cosalette/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**[Documentation](https://ff-fab.github.io/cosalette/)** ·
**[Dev Docs (latest)](https://cosalette-main.surge.sh)** ·
**[Quickstart](https://ff-fab.github.io/cosalette/getting-started/quickstart/)** ·
**[API Reference](https://ff-fab.github.io/cosalette/reference/api/)**

</div>

---

## What is cosalette?

cosalette lets you build IoT-to-MQTT bridge daemons in Python with minimal boilerplate.
You define **devices** (telemetry pollers or command handlers), register **hardware
adapters**, and the framework handles MQTT wiring, structured logging, health reporting,
error publishing, and graceful lifecycle management.

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

### Key Features

- **Declarative device registration** — define telemetry and command devices with
  `@app.telemetry()`, `@app.command()`, and `@app.device()`
  ([guide](https://ff-fab.github.io/cosalette/guides/command-device/))
- **Hexagonal architecture** — protocol-based ports keep hardware adapters swappable and
  testable ([concept](https://ff-fab.github.io/cosalette/concepts/hexagonal/))
- **Lifespan + dependency injection** — structured startup/teardown and type-based DI
  for adapters, settings, and shared state
  ([guide](https://ff-fab.github.io/cosalette/guides/lifespan/))
- **Command routing** — use sub-topic handlers and `ctx.commands()` for clean control
  flows ([guide](https://ff-fab.github.io/cosalette/guides/command-device/))
- **Flexible scheduling** — combine fixed intervals, Quartz cron, and
  `ctx.sleep_until()` wall-clock timing
- **Publish strategies** — emit on change, on cadence, or with composed rules
  ([concept](https://ff-fab.github.io/cosalette/concepts/publish-strategies/))
- **Health checks + auto-restart** — monitor adapters and recover from wedged hardware
  automatically
  ([concept](https://ff-fab.github.io/cosalette/concepts/health-reporting/))
- **Persistence** — store state with JSON, SQLite, or in-memory backends plus save
  policies ([concept](https://ff-fab.github.io/cosalette/concepts/persistence/))
- **Schema-aware integrations** — validate payloads and generate consumer artifacts from
  AsyncAPI schemas
  ([guide](https://ff-fab.github.io/cosalette/guides/schema-enforcement/))
- **AI-assisted development** — optional MCP tools plus packaged instructions for
  Copilot and other IDE-native agents
  ([guide](https://ff-fab.github.io/cosalette/guides/mcp-server/))

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
**[Dev Docs (latest)](https://cosalette-main.surge.sh)**.

| Section                                                                | What you'll find                                                 |
| ---------------------------------------------------------------------- | ---------------------------------------------------------------- |
| [Getting Started](https://ff-fab.github.io/cosalette/getting-started/) | Installation, quickstart tutorial                                |
| [Concepts](https://ff-fab.github.io/cosalette/concepts/)               | Architecture, lifecycle, health checks, filters, persistence     |
| [How-To Guides](https://ff-fab.github.io/cosalette/guides/)            | Telemetry, commands, adapters, lifespan DI, schema, testing, MCP |
| [Reference](https://ff-fab.github.io/cosalette/reference/)             | API docs, CLI options, payload schemas                           |
| [ADRs](https://ff-fab.github.io/cosalette/adr/)                        | Architecture Decision Records                                    |

## License

MIT License. See [LICENSE](LICENSE) for details.
