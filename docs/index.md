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
- **Device registration** — decorator-based API (`@app.command`, `@app.device`, `@app.telemetry`)
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
async def read_sensor() -> dict[str, object]:
    reading = sensor.read()
    return {"temperature": reading.temp, "humidity": reading.humidity}

if __name__ == "__main__":
    app.run()
```

## Explore the Documentation

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Getting Started**

    ---

    Install cosalette and build your first app in 5 minutes.

    [:octicons-arrow-right-24: Getting Started](getting-started/index.md)

-   :material-lightbulb-outline:{ .lg .middle } **Concepts**

    ---

    Understand the architecture, design patterns, and ideas behind cosalette.

    [:octicons-arrow-right-24: Concepts](concepts/index.md)

-   :material-hammer-wrench:{ .lg .middle } **How-To Guides**

    ---

    Step-by-step instructions for common tasks.

    [:octicons-arrow-right-24: How-To Guides](guides/index.md)

-   :material-book-open-variant:{ .lg .middle } **Reference**

    ---

    API reference, settings, CLI options, and payload schemas.

    [:octicons-arrow-right-24: Reference](reference/index.md)

</div>

## Architecture Decisions

All major design decisions are documented as [Architecture Decision Records](adr/index.md).

## Status

cosalette is under active development.
