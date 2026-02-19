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

- **Declarative device registration** — `@app.device()` and `@app.telemetry()`
  decorators ([guide](https://ff-fab.github.io/cosalette/guides/telemetry-device/))
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
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"temperature": 21.5, "humidity": 55.0}

if __name__ == "__main__":
    app.run()
```

See the full
[Quickstart](https://ff-fab.github.io/cosalette/getting-started/quickstart/) for a
complete walkthrough.

## Quick Start

### Prerequisites

- Python ≥ 3.14
- Docker (for DevContainer development)
- VS Code with DevContainers extension

### Setup (2 minutes)

```bash
# Clone the repository
git clone https://github.com/ff-fab/cosalette.git
cd cosalette

# Open in VS Code
code .

# In VS Code: Ctrl+Shift+P → "Dev Containers: Reopen in Container"
# DevContainer will start automatically, install dependencies, and configure everything
```

That's it! You're ready to develop.

## Common Commands

**Quick reference (via [Taskfile](https://taskfile.dev)):**

```bash
task test              # Run all tests (unit + integration + coverage)
task test:unit         # Run unit tests only
task lint              # Lint all code (Ruff check + format)
task lint:fix          # Auto-fix lint issues
task typecheck         # Type check (mypy strict)
task check             # Run all checks (lint + typecheck + test)
task pre-pr            # Full pre-PR quality gate
task docs:serve        # Serve documentation site locally
task --list            # Show all available tasks
```

## Project Structure

```
cosalette/
├── .devcontainer/              # DevContainer configuration
│   ├── devcontainer.json       # Container setup + VS Code settings
│   ├── Dockerfile              # Container image
│   └── post-create.sh          # Auto-setup script
├── packages/
│   ├── src/cosalette/          # Framework source code
│   │   ├── _app.py             # App orchestrator (composition root)
│   │   ├── _mqtt.py            # MQTT port, client, mock
│   │   ├── _health.py          # Health reporter, heartbeats, LWT
│   │   ├── _errors.py          # Structured error publishing
│   │   ├── _context.py         # Device & app contexts
│   │   ├── _settings.py        # Pydantic settings
│   │   ├── _logging.py         # JSON logging setup
│   │   ├── _cli.py             # Typer CLI builder
│   │   ├── _clock.py           # Clock port (monotonic time)
│   │   └── testing/            # Test utilities & pytest plugin
│   ├── tests/                  # Unit & integration tests
│   └── pyproject.toml          # Python project configuration
├── docs/                       # Documentation (MkDocs Material)
│   ├── getting-started/        # Quickstart & setup
│   ├── concepts/               # Architecture & design explanations
│   ├── guides/                 # How-to guides
│   ├── reference/              # API reference & schemas
│   └── adr/                    # Architecture Decision Records
├── renovate.json               # Automated dependency updates
└── zensical.toml               # Documentation site config
```

## Code Quality

- **Linting & formatting**: [Ruff](https://docs.astral.sh/ruff/) (88-char line length,
  double quotes)
- **Type checking**: [mypy](https://mypy-lang.org/) (strict mode)
- **Testing**: [pytest](https://docs.pytest.org/) with pytest-asyncio
- **Coverage**: ≥80% threshold (lines and branches)
- **Pre-commit**: EditorConfig, trailing whitespace, codespell, Ruff, mypy

All tools are **auto-configured in DevContainer** via `.devcontainer/devcontainer.json`.
Format on save is enabled by default.

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
