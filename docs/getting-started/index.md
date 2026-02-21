---
icon: material/rocket-launch
---

# Getting Started

Welcome to cosalette — a Python framework for building MQTT device daemons.

cosalette handles the infrastructure (MQTT connectivity, configuration, logging,
health reporting, error handling) so you can focus on your device's domain logic.

## What You'll Build

cosalette is **FastAPI for MQTT daemons**. If you've ever written a Python script that
reads a sensor, formats JSON, publishes to an MQTT broker, and runs in a `while True`
loop — cosalette replaces all the boilerplate around that loop.

You declare your devices with decorators, and the framework handles:

- **MQTT connectivity** — connection, reconnection, Last Will & Testament
- **Configuration** — environment variables and `.env` files via pydantic-settings
- **Structured logging** — JSON or text, with device-scoped correlation
- **Health reporting** — per-device availability, LWT crash detection, online/offline status
- **Error isolation** — one device crashing doesn't take down the others
- **Graceful shutdown** — SIGTERM/SIGINT handling, orderly teardown
- **Testing** — first-class test doubles and a harness for integration tests

A minimal cosalette app looks like this:

```python
import cosalette

app = cosalette.App(name="mybridge", version="0.1.0")

@app.telemetry("sensor", interval=10.0)
async def sensor() -> dict[str, object]:
    return {"temperature": 21.5, "humidity": 55}

app.run()
```

That's a fully operational MQTT daemon — with structured logging, health reporting,
graceful shutdown, and a CLI with `--dry-run`, `--log-level`, and `--version` flags —
all from 7 lines of code.

<div class="grid cards" markdown>

-   :material-clock-fast:{ .lg .middle } **Quickstart**

    ---

    Build your first cosalette app step by step — from zero to a
    working telemetry daemon with tests.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

</div>

## Installation

cosalette is published on [PyPI](https://pypi.org/project/cosalette/).

=== "uv (recommended)"

    ```bash
    uv add cosalette
    ```

=== "pip"

    ```bash
    pip install cosalette
    ```

??? note "Alternative: install from Git"

    To install the latest development version directly from the repository:

    ```bash
    uv add "cosalette @ git+https://github.com/ff-fab/cosalette.git"
    ```

    Or for a local editable install:

    ```bash
    git clone https://github.com/ff-fab/cosalette.git
    cd cosalette
    uv pip install -e packages/
    ```

## Requirements

- Python 3.14+
- An MQTT broker (e.g. [Mosquitto](https://mosquitto.org/))

## Next Steps

Once you have cosalette installed:

1. **[Quickstart](quickstart.md)** — Build a telemetry daemon from scratch, add
   configuration, and write your first test.
2. **[Architecture](../concepts/architecture.md)** — Understand the composition-root
   pattern and how the framework orchestrates your devices.
3. **[Device Archetypes](../concepts/device-archetypes.md)** — Learn about the two
   fundamental device patterns: telemetry and command & control.
