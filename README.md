# cosalette

an opinionated Python framework for building IoT-to-MQTT bridge applications

## Quick Start

### Prerequisites

- Docker and Docker Compose (for DevContainer)
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
task test              # Run all tests
task lint              # Lint all code
task lint:fix          # Auto-fix lint issues
task check             # Run all checks (lint + typecheck + test)
task docs:build        # Build documentation site
task docs:serve        # Serve documentation site locally
task plan              # Show project phase progress
task plan:ui           # Interactive project progress view
task --list            # Show all available tasks
```

## Project Structure

```
cosalette/
├── .devcontainer/              # DevContainer configuration
│   ├── devcontainer.json       # Container setup + VS Code settings
│   ├── Dockerfile              # Container image
│   ├── post-create.sh          # Auto-setup script
├── packages/
│   ├── src/cosalette/      # Source code
│   ├── tests/                  # All tests
│   └── pyproject.toml          # Python configuration (Ruff, mypy, pytest)
├── docs/                       # Documentation
│   ├── adr/                    # Architecture Decision Records
│   └── TODO/                   # Pending decisions and deferred topics
├── VERSIONING.md               # Version management documentation
├── mkdocs.yml                  # Documentation site config
```

## Code Quality

### Formatting

- **Python**: Ruff (88-char line length, double quotes)
- **Frontend**: Prettier (88-char line length, single quotes)
- **All files**: LF line endings (enforced via `.gitattributes`)

### Linting

- **Python**: Ruff (comprehensive rule set)
- **TypeScript**: ESLint

### Type Checking

- **Python**: mypy (strict mode)
- **TypeScript**: TypeScript compiler (strict mode)

All tools are **auto-configured in DevContainer** via `.devcontainer/devcontainer.json`.
Format on save is enabled by default.

## Versioning

Versions are automatically derived from git tags using `setuptools_scm`:

```bash
# Current version (from git tag or dev counter)
python -c "from cosalette import __version__; print(__version__)"

# Tag a release
git tag v0.1.0
```

See [VERSIONING.md](VERSIONING.md) for details.

## License

MIT License. See [LICENSE](LICENSE) for details.
