# Contributing to cosalette

Thank you for your interest in contributing to cosalette! This guide covers everything
you need to get a development environment running and start making changes.

## Prerequisites

- Python ≥ 3.14
- Docker (for DevContainer development)
- VS Code with DevContainers extension

## Setup (2 minutes)

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
├── .github/
│   ├── agents/                 # AI agent configurations
│   ├── skills/                 # AI skill definitions
│   ├── workflows/              # CI/CD (tests, docs, release)
│   └── ...                     # Additional GitHub automation, prompts, templates, etc.
├── crates/
│   └── cosalette-filters-rs/   # Rust signal filters (PyO3)
├── packages/
│   ├── src/cosalette/          # Framework source code
│   │   ├── _app/               # App orchestrator (composition root)
│   │   │   ├── __init__.py     # App class + re-exports
│   │   │   ├── _adapter.py     # adapter() registration
│   │   │   ├── _command.py     # command() registration
│   │   │   ├── _configure.py   # on_configure(), state()
│   │   │   ├── _device.py      # device() registration
│   │   │   ├── _helpers.py     # shared private functions
│   │   │   ├── _lifecycle.py   # run(), cli(), _run_async()
│   │   │   ├── _periodic.py    # periodic() registration
│   │   │   ├── _stream.py      # stream() registration
│   │   │   └── _telemetry.py   # telemetry() registration + validators
│   │   ├── _adapter_lifecycle.py # Adapter health + auto-restart
│   │   ├── _cli.py             # Typer CLI builder
│   │   ├── _clock.py           # Clock port (monotonic time)
│   │   ├── _command.py         # Command dataclass + routing
│   │   ├── _context.py         # Device & app contexts
│   │   ├── _cron.py            # Quartz cron scheduling
│   │   ├── _errors.py          # Structured error publishing
│   │   ├── _health.py          # Health reporter, heartbeats, LWT
│   │   ├── _injection.py       # Type-based dependency injection
│   │   ├── _logging.py         # JSON logging setup
│   │   ├── _mcp/               # MCP server for AI tooling
│   │   ├── _mqtt.py            # MQTT port, client, mock
│   │   ├── _persist.py         # Persistence port + save policies
│   │   ├── _schema/            # AsyncAPI schema enforcement
│   │   ├── _settings.py        # Pydantic settings
│   │   ├── _strategies.py      # Publish strategies (on-change, cadence)
│   │   ├── _wiring.py          # Dependency wiring + bootstrap orchestration
│   │   └── testing/            # Test utilities & pytest plugin
│   ├── tests/
│   │   ├── unit/               # Unit tests (no external dependencies)
│   │   │   └── conftest.py     # Shared fixtures (inherited by all sub-dirs)
│   │   ├── integration/        # Integration tests (require mock servers)
│   │   ├── benchmarks/         # pytest-benchmark performance tests
│   │   └── fixtures/           # Shared test data and helpers
│   └── pyproject.toml          # Python project configuration
├── docs/                       # Documentation (Zensical)
│   ├── getting-started/        # Quickstart & setup
│   ├── concepts/               # Architecture & design explanations
│   ├── guides/                 # How-to guides
│   ├── reference/              # API reference & schemas
│   └── adr/                    # Architecture Decision Records
├── Cargo.toml                  # Rust workspace configuration
├── renovate.json               # Automated dependency updates
└── zensical.toml               # Documentation site config
```

## Code Quality

- **Linting & formatting**: [Ruff](https://docs.astral.sh/ruff/) (88-char line length,
  double quotes)
- **Type checking**: [ty](https://github.com/astral-sh/ty) (strict mode)
- **Testing**: [pytest](https://docs.pytest.org/) with pytest-asyncio
- **Coverage**: ≥80% threshold (lines and branches)
- **Pre-commit**: EditorConfig, trailing whitespace, codespell, Ruff, mypy

All tools are **auto-configured in DevContainer** via `.devcontainer/devcontainer.json`.
Format on save is enabled by default.

## Workflow

This project follows **GitHub Flow**:

1. Create a feature branch from `main`
2. Make changes with [conventional commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `docs:`, `chore:`, etc.)
3. Run `task pre-pr` to pass all quality gates
4. Open a pull request — never push directly to `main`

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
