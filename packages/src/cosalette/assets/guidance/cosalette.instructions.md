---
description: 'cosalette framework development guidance for AI agents'
applyTo: '**/*.py'
---

# cosalette Framework Instructions

Quick-reference only. For depth: `cosalette ai help <topic>`
Topics: `telemetry` · `testing` · `configuration` · `architecture` · `commands` · `health` · `scheduling` · `resilience` · `sub-entities` · `triggerable` · `multi-device` · `contracts` · `manifest`

## Archetype — Pick One

| Archetype | When | Decorator |
|-----------|------|-----------|
| **Telemetry** | Periodic sensor read / scheduled publish | `@app.telemetry(name, interval=N)` |
| **Command** | Handle inbound MQTT `…/set` payloads | `@app.command(name)` |
| **Device** | Explicit `while` loop / state machine | `@app.device(name)` |

Default to **telemetry**. Multiple similar devices → `name=lambda s: {…}` dict form (not `@app.on_configure`).
See `cosalette ai help architecture`.

## `main.py` — Declarative Only

```python
import cosalette

app = cosalette.App(name="mybridge", version="0.1.0", settings_class=MySettings)
app.adapter(SensorPort, "myapp.adapters:SensorAdapter", dry_run="myapp.adapters:DryRunAdapter")

@app.telemetry("sensor", interval=cosalette.setting_ref("poll_interval"))
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": await ctx.adapter(SensorPort).read()}

if __name__ == "__main__":
    app.run()
```

Rules:
- Decorators and `app.run()` only — no business logic in `main.py`
- `ctx.sleep(N)` — never `asyncio.sleep()` or `time.sleep()` in device coroutines
- `ctx.adapter(Port)` — never import adapter classes inside device handlers
- `ctx.settings` — never import settings as a global

## Testing — Mandatory Rules

```python
# conftest.py
pytest_plugins = ["cosalette.testing._plugin"]
# Fixtures: mock_mqtt · fake_clock · device_context
```

**Never patch `time.monotonic`, `asyncio.sleep`, or `time.sleep` globally.**
asyncio uses these internally; global patches corrupt loop timing (Python 3.14+ fails with an infinite loop / timeout).

| Layer | Tool |
|-------|------|
| Domain (pure functions, parsers) | Plain pytest — zero cosalette imports |
| Device handler | `device_context` fixture |
| Full MQTT round-trip | `AppHarness.create()` |

Device coroutines call `ctx.sleep(N)` — the `fake_clock` fixture intercepts this, advancing
virtual time with no wall-clock delay.

When domain code holds a bare `time_module` reference, swap the **module object**, not the attribute:

```python
import myapp.domain.device as mod
mod.time_module = fake_time_module   # ✓ only intercepts calls through this module

# NOT: mock.patch("myapp.domain.device.time_module.monotonic", ...)  # ✗ patches globally
```

See `cosalette ai help testing`.

## Configuration

```python
from pydantic_settings import SettingsConfigDict

class MySettings(cosalette.Settings):
    poll_interval: float = 30.0
    model_config = SettingsConfigDict(env_prefix="MYAPP_", env_nested_delimiter="__")
```

Built-in MQTT settings include `mqtt.tls`, `mqtt.tls_ca_file`, and mutual-TLS
`mqtt.tls_cert_file`/`mqtt.tls_key_file` for broker TLS on port 8883.

See `cosalette ai help configuration`.

## Ports & Adapters

```python
# String path → lazy import (hardware libs absent on dev machines)
app.adapter(SensorPort, "myapp.adapters:SensorAdapter", dry_run="myapp.adapters:DryRunAdapter")
```

Domain layer must never import cosalette or adapters. See `cosalette ai help architecture`.

---

Refresh this file: `cosalette ai init`
Inspect registrations: `cosalette manifest myapp.main:app [--table]`
