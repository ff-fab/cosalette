# Command Handler Redesign: @app.command() Decorator

*2026-02-20T16:01:09Z by Showboat 0.5.0*

Added @app.command() as a new first-class decorator for registering command handlers as standalone module-level functions. This replaces the need for @app.device() + @ctx.on_command closures in most cases. Parameters named 'topic' and 'payload' receive MQTT values; all other params are injected by type. Return a dict to auto-publish state. Also added parameter kind validation, integration tests, and comprehensive documentation updates across 6 docs files.

```bash
cd /workspace && uv run pytest packages/tests/unit/test_command.py --tb=short -q 2>&1 | tail -3
```

```output
packages/tests/unit/test_command.py ......................               [100%]

============================== 22 passed in 0.93s ==============================
```

```bash
cd /workspace && uv run pytest packages/tests/unit/test_injection.py --tb=short -q 2>&1 | tail -3
```

```output
packages/tests/unit/test_injection.py .....                              [100%]

============================== 5 passed in 0.03s ===============================
```

```bash
cd /workspace && uv run pytest packages/tests/ --tb=short -q 2>&1 | grep -oP '\d+ passed' | head -1
```

```output
340 passed
```
