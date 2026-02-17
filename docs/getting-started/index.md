---
icon: material/rocket-launch
---

# Getting Started

Welcome to cosalette — a Python framework for building MQTT device daemons.

cosalette handles the infrastructure (MQTT connectivity, configuration, logging,
health reporting, error handling) so you can focus on your device's domain logic.

<div class="grid cards" markdown>

-   :material-clock-fast:{ .lg .middle } **Quickstart**

    ---

    Build your first cosalette app in 5 minutes.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

</div>

## Installation

```bash
pip install cosalette
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add cosalette
```

## Requirements

- Python 3.14+
- An MQTT broker (e.g. [Mosquitto](https://mosquitto.org/))
