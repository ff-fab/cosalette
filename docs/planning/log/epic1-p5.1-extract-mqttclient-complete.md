## Epic 1 Phase Complete: P5.1 Extract MqttClient

Extracted the production `MqttClient` adapter from `_mqtt.py` into a dedicated
`_mqtt_client.py` module, leaving `_mqtt.py` as a clean ports-and-protocols module.
Backward compatibility preserved via re-export.

**Files created/changed:**

- `packages/src/cosalette/_mqtt_client.py` (new — ~266 lines)
- `packages/src/cosalette/_mqtt.py` (reduced from 588 to ~231 lines)
- `packages/tests/unit/test_mqtt.py` (3 patch targets updated)

**Functions created/changed:**

- `MqttClient` class moved to `_mqtt_client.py` (with `start`, `stop`,
  `_connection_loop`, `_dispatch`, `_build_will`, `_extract_password`)
- `_mqtt.py` now re-exports `MqttClient` from `_mqtt_client` for backward compat

**Tests created/changed:**

- `test_mqtt.py`: 3 `patch()` targets changed from `cosalette._mqtt.random.uniform`
  to `cosalette._mqtt_client.random.uniform`
- All 866 tests passing

**Review Status:** APPROVED

**Git Commit Message:**

```
refactor: extract MqttClient from _mqtt.py into _mqtt_client.py

- Move production MqttClient adapter to dedicated _mqtt_client.py module
- Keep _mqtt.py as clean ports/protocols module with value objects
- Update patch targets in test_mqtt.py for new module location
- Preserve backward compatibility via re-export in _mqtt.py
```
