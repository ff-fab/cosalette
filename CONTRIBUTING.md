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

Integration tests are split into two suites based on external service requirements:

| Task                         | Requires Docker                    | Runs on PR / push |
| ---------------------------- | ---------------------------------- | ----------------- |
| `task test:integration`      | No                                 | Yes               |
| `task test:mqtt`             | Yes (Mosquitto via testcontainers) | No                |
| `task test:integration:full` | Yes                                | No                |

- **`task test:integration`** -- fast, no external services; covered by PR, push, and
  `task pre-pr` gates. MQTT tests are excluded via the `-m 'not mqtt'` pytest marker.
- **`task test:mqtt`** -- spins up a real Mosquitto broker via testcontainers; requires
  a Docker Engine socket. Run inside the DevContainer, which provides Docker-in-Docker.
- **`task test:integration:full`** -- runs both suites together.

MQTT tests are intentionally excluded from default PR/push/`task pre-pr` gates. Full
MQTT validation runs via manual CI workflow dispatch (**Actions -> Integration Tests**)
and as the Release Please full-suite gate before GitHub release publication and
TestPyPI.

### Running MQTT tests locally

The DevContainer provides Docker-in-Docker, so no extra setup is needed:

```bash
task test:mqtt              # MQTT suite only (Mosquitto via testcontainers)
task test:integration:full  # All integration tests including MQTT
```

### CI job architecture

All code CI jobs (lint, unit, integration, complexity) run inside the DevContainer via
`.github/actions/devcontainer-run`. This keeps Python 3.14, Rust/maturin, and uv
versions identical between local development and CI, with a single toolchain source of
truth. `docs.yml` also runs inside the DevContainer (Docker login + Buildx +
devcontainers/ci) to use the same build toolchain. Only `codeql` and
`dependency-submission` run on bare runners.

Fast gates do not require Docker at the command level: `task test` and
`task ci:test:integration` run plain `uv run` invocations that exclude the `mqtt`
marker. The devcontainer-run wrapper adds Docker only as the job execution environment.

**Why all code jobs use devcontainer-run (not bare runners):** The maturin native
extension must compile against the exact Python ABI baked into the devcontainer image.
Splitting toolchain setup across the Dockerfile and individual workflow steps creates a
duplicated version-pinning surface (Python, Rust, uv, task) and risks maturin wheel-ABI
drift -- "works in devcontainer, fails in CI" failures that are expensive to diagnose.
Keeping all code jobs in the devcontainer ensures devcontainer and CI behavior stay
identical by construction. Devcontainer cache overhead is bounded (~60-120s warm, ~5-8
min on cold cache) and is triggered by `.devcontainer/**` changes, scheduled rebuilds,
or manual dispatch -- not routine PRs.

**Revisit condition:** Split lint/unit jobs to bare runners only after a small prototype
or CI benchmark demonstrates that bare-runner toolchain setup is simpler and faster
_without_ introducing maturin/ABI parity regressions. The full analysis and option
comparison are in `.github/planning/cos-4a2-optional-docker-final-gate-plan.md`.

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
