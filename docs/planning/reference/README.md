# Reference Code — Extracted from velux2mqtt

These files are the production code from the `velux2mqtt` project (branch
`feat/pydantic-config-schema` at commit `7ab67ad`). They serve as the
**implementation reference** for cosalette framework development.

Each file has been extracted verbatim. The cosalette framework will
**generalise** these into framework-provided components, stripping the
velux2mqtt-specific parts.

## Mapping: velux2mqtt → cosalette

| velux2mqtt file | cosalette component | What changes |
| --------------- | ------------------- | ------------ |
| `mqtt_client.py` | `cosalette._mqtt` | Remove velux-specific topic filtering (`/actual`), add LWT support, generalise settings reference |
| `error_publisher.py` | `cosalette._errors` | Remove velux-specific error type mapping, make error types pluggable |
| `log_format.py` | `cosalette._logging` | Replace hardcoded `"velux2mqtt"` service name with `app.name` |
| `clock.py` | `cosalette._clock` | Nearly identical — just re-namespace |
| `config.py` | `cosalette._settings` | Extract `MqttSettings` + `LoggingSettings` as framework base; remove actuator/GPIO config |
| `protocols.py` | `cosalette._clock` (ClockPort only) | `MqttPort` becomes internal; `GpioPort` is project-specific; `ClockPort` is framework-provided |
| `main.py` | `cosalette._app` | Replace with `App` class + decorator registration + lifecycle management |
| `handlers.py` | `cosalette._router` | Generalise into `TopicRouter` — `{prefix}/{device}/set` pattern extraction |

## How to Use These Files

1. **Read the module docstrings** — they explain design decisions that
   should carry forward into cosalette
2. **Identify the generic vs. specific** — anything referencing "actuator",
   "GPIO", "position", or "velux" is project-specific and should NOT be
   in the framework
3. **Preserve the patterns** — reconnection loop, callback dispatch,
   structured error payloads, Protocol-based ports, lazy imports
