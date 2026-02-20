# cosalette

An opinionated Python framework for building IoT-to-MQTT bridge applications.

[![CI](https://github.com/ff-fab/cosalette/actions/workflows/ci.yml/badge.svg)](https://github.com/ff-fab/cosalette/actions/workflows/ci.yml)
[![Docs](https://github.com/ff-fab/cosalette/actions/workflows/docs.yml/badge.svg)](https://ff-fab.github.io/cosalette/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.14-blue)](https://www.python.org/)

**[Documentation](https://ff-fab.github.io/cosalette/)** ·
**[Quickstart](https://ff-fab.github.io/cosalette/getting-started/quickstart/)** ·
**[API Reference](https://ff-fab.github.io/cosalette/reference/api/)**

---

## What is cosalette?

cosalette lets you build IoT-to-MQTT bridge daemons in Python with minimal boilerplate.
You define **devices** (telemetry pollers or command handlers), register **hardware
adapters**, and the framework handles MQTT wiring, structured logging, health reporting,
error publishing, and graceful lifecycle management.

### Key Features

- **Declarative device registration** — `@app.command()`, `@app.device()`, and
  `@app.telemetry()` decorators
  ([guide](https://ff-fab.github.io/cosalette/guides/command-device/))
- **Hexagonal architecture** — protocol-based ports with swappable adapters
  ([concept](https://ff-fab.github.io/cosalette/concepts/hexagonal/))
- **Structured JSON logging** — per-device context, configurable levels
  ([concept](https://ff-fab.github.io/cosalette/concepts/logging/))
- **Health & heartbeats** — LWT crash detection, periodic JSON heartbeats, per-device
  availability
  ([concept](https://ff-fab.github.io/cosalette/concepts/health-reporting/))
- **Structured error publishing** — domain errors published to MQTT with type mapping
  ([concept](https://ff-fab.github.io/cosalette/concepts/error-handling/))
- **Pydantic settings** — type-safe configuration from env vars and `.env` files
  ([guide](https://ff-fab.github.io/cosalette/guides/configuration/))
- **CLI for free** — `--dry-run`, `--version`, `--log-level`, `--env-file` via Typer
  ([reference](https://ff-fab.github.io/cosalette/reference/cli/))
- **Test-friendly** — `MockMqttClient`, `FakeClock`, pytest fixtures included
  ([guide](https://ff-fab.github.io/cosalette/guides/testing/))

## Quick Example

```python
import cosalette

app = cosalette.App(name="weather2mqtt", version="0.1.0")

@app.telemetry("sensor", interval=5.0)
async def sensor() -> dict[str, object]:
    return {"temperature": 21.5, "humidity": 55.0}

if __name__ == "__main__":
    app.run()
```

See the full
[Quickstart](https://ff-fab.github.io/cosalette/getting-started/quickstart/) for a
complete walkthrough.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, commands, project
structure, and code quality standards.

## Documentation

Full documentation is published at
**[ff-fab.github.io/cosalette](https://ff-fab.github.io/cosalette/)**.

| Section                                                                | What you'll find                                       |
| ---------------------------------------------------------------------- | ------------------------------------------------------ |
| [Getting Started](https://ff-fab.github.io/cosalette/getting-started/) | Installation, quickstart tutorial                      |
| [Concepts](https://ff-fab.github.io/cosalette/concepts/)               | Architecture, MQTT topics, lifecycle, hexagonal design |
| [How-To Guides](https://ff-fab.github.io/cosalette/guides/)            | Step-by-step guides for each feature                   |
| [Reference](https://ff-fab.github.io/cosalette/reference/)             | API docs, CLI options, payload schemas                 |
| [ADRs](https://ff-fab.github.io/cosalette/adr/)                        | Architecture Decision Records                          |

## License

MIT License. See [LICENSE](LICENSE) for details.
