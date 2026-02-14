## Epic Phase 2 Complete: MQTT Client (_mqtt.py)

Generalized the velux2mqtt MQTT client into a framework-level module with protocol,
real client, mock test double, and null adapter. Includes LWT support via WillConfig,
auto-reconnect with subscription restore, and lazy aiomqtt import per ADR-006.

**Files created/changed:**

- `packages/src/cosalette/_mqtt.py` (created)
- `packages/tests/unit/test_mqtt.py` (created)
- `packages/src/cosalette/__init__.py` (modified — new MQTT exports)
- `docs/TODO/T-mqtt-reexport.md` (created — gate task deliberation doc)

**Functions created/changed:**

- `MessageCallback` type alias — `Callable[[str, str], Awaitable[None]]`
- `WillConfig` frozen dataclass — LWT abstraction decoupled from aiomqtt
- `MqttPort` Protocol — runtime_checkable port contract (publish/subscribe)
- `MqttClient` dataclass — real aiomqtt adapter with reconnect loop, LWT, dispatch
- `MockMqttClient` dataclass — test double with deliver(), reset(), get_messages_for()
- `NullMqttClient` dataclass — silent no-op adapter

**Tests created/changed:**

- `TestWillConfig` — frozen, defaults, custom values (3 tests)
- `TestMqttPortProtocol` — conformance for all 3 adapters + negative case (4 tests)
- `TestNullMqttClient` — publish/subscribe no-ops (2 tests)
- `TestMockMqttClientPublish` — recording, count, get_messages_for (3 tests)
- `TestMockMqttClientSubscribe` — recording, count (2 tests)
- `TestMockMqttClientCallbacks` — register, deliver, order, error propagation (4 tests)
- `TestMockMqttClientReset` — clears all state (1 test)
- `TestMqttClientLifecycle` — start/stop/is_connected/idempotent (4 tests)
- `TestMqttClientPublish` — RuntimeError when disconnected, delegates to inner (2 tests)
- `TestMqttClientSubscribe` — tracks, subscribes with qos if connected (2 tests)
- `TestMqttClientWill` — WillConfig conversion, None passthrough (2 tests)
- `TestMqttClientConnect` — SecretStr password extraction, subscription restore (2 tests)
- `TestMqttClientDispatch` — decode, None skip, error logged, callback order (4 tests)
- `TestMqttClientReconnect` — retry after error with interval sleep (1 test)
- Total: 36 new tests (90 total), 95.6% coverage

**Review Status:** APPROVED with minor findings — all addressed

**Git Commit Message:**

```
feat: add MQTT client port and adapters (_mqtt.py)

- MqttPort protocol, MqttClient with auto-reconnect and LWT
- MockMqttClient test double and NullMqttClient no-op adapter
- WillConfig frozen dataclass abstracting aiomqtt.Will
- 36 tests covering lifecycle, dispatch, reconnection, credentials
- Gate task for MockMqttClient re-export to cosalette.testing
```
