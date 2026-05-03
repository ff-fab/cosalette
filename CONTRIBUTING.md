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
task typecheck         # Type check (ty strict)
task check             # Run all checks (lint + typecheck + test)
task pre-pr            # Full pre-PR quality gate
task docs:serve        # Serve documentation site locally
task --list            # Show all available tasks
```

## Integration Tests

Integration tests are split into two suites:

| Task                         | Requires Docker                    | Runs on PR / push |
| ---------------------------- | ---------------------------------- | ----------------- |
| `task test:integration`      | No                                 | Yes               |
| `task test:mqtt`             | Yes (Mosquitto via testcontainers) | No                |
| `task test:integration:full` | Yes                                | No                |

- **`task test:integration`** — fast, no external services; covered by PR, push, and
  `task pre-pr` gates.
- **`task test:mqtt`** — spins up a real Mosquitto broker; requires Docker Engine.
- **`task test:integration:full`** — runs both suites together.

MQTT tests are intentionally excluded from default PR/push/`task pre-pr` gates. They run
via manual CI workflow dispatch (**Actions → Integration Tests**) and as the Release
Please full-suite gate before TestPyPI.

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
│   │   ├── _adapter_lifecycle.py # Adapter health + auto-restart
│   │   ├── _ai_content/        # AI help content (topics, prime, what's-new)
│   │   ├── _cli.py             # Typer CLI builder
│   │   ├── _clock.py           # Clock port (monotonic time)
│   │   ├── _command.py         # Command dataclass + routing
│   │   ├── _context.py         # Device & app contexts
│   │   ├── _cron/              # Quartz cron scheduling
│   │   ├── _errors.py          # Structured error publishing
│   │   ├── _health/            # Health reporting, heartbeats, LWT
│   │   ├── _injection.py       # Type-based dependency injection
│   │   ├── _logging.py         # JSON logging setup
│   │   ├── _mcp/               # MCP server for AI tooling
│   │   ├── _mqtt/              # MQTT port, client, router
│   │   ├── _package_cli/       # `cosalette package` CLI sub-commands
│   │   ├── _persistence/       # Persistence port + save policies
│   │   ├── _runners/           # Telemetry + command runner implementations
│   │   ├── _schema/            # AsyncAPI schema enforcement
│   │   ├── _settings/          # Pydantic settings
│   │   ├── _strategies/        # Publish strategies (on-change, cadence)
│   │   ├── _wiring/            # Dependency wiring + bootstrap orchestration
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
- **Pre-commit**: EditorConfig, trailing whitespace, codespell, Ruff, ty

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
