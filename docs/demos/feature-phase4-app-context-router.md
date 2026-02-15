# Phase 4: App + DeviceContext + TopicRouter

*2026-02-15T11:24:29Z by Showboat 0.5.0*

Implemented the core decorator API for cosalette: App class (composition root), DeviceContext (per-device runtime context), AppContext (lifecycle hook context), and TopicRouter (MQTT command dispatch). 74 new tests, all 220 unit tests passing at 94.5% coverage.

```bash
cd /workspace/packages && uv run python -c "import cosalette; print('App:', cosalette.App); print('DeviceContext:', cosalette.DeviceContext); print('AppContext:', cosalette.AppContext)"
```

```output
App: <class 'cosalette._app.App'>
DeviceContext: <class 'cosalette._context.DeviceContext'>
AppContext: <class 'cosalette._context.AppContext'>
```

```bash
cd /workspace && task test:unit 2>&1 | tail -1 | cut -d' ' -f1-3
```

```output
============================= 220 passed
```
