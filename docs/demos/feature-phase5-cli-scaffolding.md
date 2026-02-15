# Phase 5: CLI Scaffolding with Typer

*2026-02-15T16:34:14Z by Showboat 0.5.0*

Added Typer-based CLI scaffolding via build_cli() factory. App.run() now delegates to Typer with --version, --dry-run, --log-level, --log-format, --env-file options. Exit codes: 0 (ok), 1 (config), 3 (runtime). 15 new tests, 235 total passing, 94.95% coverage.

```bash
cd /workspace && task test:unit 2>&1 | grep -oE '[0-9]+ passed'
```

```output
235 passed
```

```bash
cd /workspace/packages && uv run python -c "from cosalette._cli import build_cli, EXIT_OK, EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR; print(f'EXIT_OK={EXIT_OK}, EXIT_CONFIG_ERROR={EXIT_CONFIG_ERROR}, EXIT_RUNTIME_ERROR={EXIT_RUNTIME_ERROR}')"
```

```output
EXIT_OK=0, EXIT_CONFIG_ERROR=1, EXIT_RUNTIME_ERROR=3
```
