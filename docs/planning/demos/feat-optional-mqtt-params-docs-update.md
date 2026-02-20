# Optional MQTT params + docs update

*2026-02-20T19:40:42Z by Showboat 0.5.0*

Made topic/payload parameters optional in @app.command() handlers. Handlers now only receive the MQTT message values they declare in their signature — no more _topic workaround. Also updated 11 documentation pages to reflect @app.command() as the third device archetype and fixed 3 broken links.

```bash
cd /workspace && uv run pytest packages/tests/unit/test_command.py -q --tb=no 2>&1 | tail -1
```

```output
============================== 28 passed in 1.09s ==============================
```

```bash
cd /workspace && task docs:build 2>&1 | grep -c 'Build finished'
```

```output
1
```
