---
title: Home
description: cosalette — an opinionated Python framework for building IoT-to-MQTT bridge applications
---

# cosalette

**An opinionated Python framework for building IoT-to-MQTT bridge applications.**

Think "FastAPI for MQTT daemons."

## What is cosalette?

cosalette provides the common infrastructure that every IoT-to-MQTT bridge needs:

- **MQTT lifecycle** — connection, reconnection, Last Will and Testament
- **Device registration** — decorator-based API (`@app.device`, `@app.telemetry`)
- **Configuration** — pydantic-settings with environment variables and `.env` files
- **Structured logging** — JSON (NDJSON) for production, text for development
- **Error reporting** — structured errors published to MQTT topics
- **Health monitoring** — per-device availability and app-level status with LWT
- **CLI** — `--dry-run`, `--version`, `--log-level`, `--log-format`, `--env-file`
- **Testing** — `cosalette.testing` module with pytest fixtures and test doubles

## Quick Example

```python
import cosalette

app = cosalette.App(name="gas2mqtt", version="0.1.0")

@app.telemetry("sensor", interval=30.0)
async def read_sensor(ctx: cosalette.DeviceContext) -> dict:
    reading = sensor.read()
    return {"temperature": reading.temp, "humidity": reading.humidity}

if __name__ == "__main__":
    app.run()
```

## Architecture Decisions

All major design decisions are documented as [Architecture Decision Records](adr/index.md).

## Status

cosalette is in active early development (Phase 1). See the
[framework proposal](planning/cosalette-framework-proposal.md) for the full design.
