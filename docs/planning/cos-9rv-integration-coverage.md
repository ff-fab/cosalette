# Planning: Integration-Test Coverage Inventory and Gap Closure

**Epic:** cos-9rv -- Integration-test coverage for Router, typed payloads, and new
archetypes

**Goal:** Identify what the existing integration-test suite covers, document the gaps
against implemented archetypes and features, and define a concrete closure plan so that
every documented pattern has at least one deterministic integration test.

---

## Context

The test suite currently has five integration files:

| File | Scope |
|------|-------|
| `test_integration.py` | Core lifecycle, command dispatch, telemetry publish, manual device, commands iterator, subtopics, coalescing groups |
| `test_cross_feature_integration.py` | Command + telemetry + persistence combined, stress, config reload, state machine, failure isolation |
| `test_persistence_integration.py` | `DeviceStore` injection, persist-on-change and persist-on-interval policies |
| `test_health_check_integration.py` | Named-device health and availability transitions |
| `test_mqtt_integration.py` | Broker-backed round-trip, retain semantics, reconnect, resubscribe |

The harness (`AppHarness` / `FakeClock`) supports deterministic in-process testing
without a broker.  Broker-backed tests in `test_mqtt_integration.py` should remain
limited to broker-specific semantics (retain, QoS, reconnect); all deterministic
scenarios use `AppHarness`.

---

## Coverage Matrix

| Archetype / Feature | Existing coverage | Gap | Planned action |
|---------------------|-------------------|-----|----------------|
| Core lifecycle (connect/disconnect) | `test_integration.py` | None | -- |
| App-level command dispatch | `test_integration.py` | None | -- |
| App-level telemetry publish | `test_integration.py` | None | -- |
| Manual device | `test_integration.py` | None | -- |
| Commands iterator | `test_integration.py` | None | -- |
| Subtopic routing | `test_integration.py` | None | -- |
| Coalescing groups | `test_integration.py` | None | -- |
| Command + telemetry + persistence | `test_cross_feature_integration.py` | None | -- |
| Stress (burst publish) | `test_cross_feature_integration.py` | None | -- |
| Config reload | `test_cross_feature_integration.py` | None | -- |
| State machine | `test_cross_feature_integration.py` | None | -- |
| Failure isolation | `test_cross_feature_integration.py` | None | -- |
| DeviceStore injection | `test_persistence_integration.py` | None | -- |
| Persist-on-change policy | `test_persistence_integration.py` | None | -- |
| Persist-on-interval policy | `test_persistence_integration.py` | None | -- |
| Health and availability transitions | `test_health_check_integration.py` | None | -- |
| Broker retain / reconnect | `test_mqtt_integration.py` | None | -- |
| **Router composition / prefix routing** | `TestRouterComposition` in `test_integration.py` | None | Covered |
| **TopicRouter slash-composed subtopics** | `TestRouterComposition.test_slash_composed_command_subtopic_live_mqtt` in `test_integration.py` | None | Covered |
| **Triggerable telemetry under router prefix** | `TestTriggerableTelemetryUnderRouterPrefix` in `test_integration.py` | None | Covered |
| **Typed command payloads (Pydantic model)** | `TestTypedCommandPayload` in `test_integration.py` | None | Covered |
| **Typed telemetry payloads (Pydantic model)** | `TestTypedTelemetryReturn` in `test_integration.py` | None | Covered |
| **Retry / backoff regression** | `TestRetryBackoff` in `test_cross_feature_integration.py` | None | Covered |
| **Stream proxy / lifecycle ownership** | `TestStreamProxyLifecycleOwnership` in `test_integration.py` | None | Covered |
| **Periodic / background task archetype** | `TestPeriodicTaskArchetype` in `test_integration.py` | None | Covered |

---

## Gap Details

### Router composition and prefix routing

Router and `include_router` prefixes are single MQTT name segments (no `/` allowed
inside an individual prefix).  Slash-composed names such as `building/floor1/temp`
are produced by composition: `Router(prefix="floor1")` mounted via
`app.include_router(router, prefix="building")`.  Previously no integration test
verified that an `AppHarness` dispatches `building/floor1/temp` to a handler on
such a composed router, nor that two routers at distinct composed prefixes receive
only their own messages.

**Implemented** in `TestRouterComposition` (`test_integration.py`):
- `test_two_routers_no_crosstalk` -- two routers at distinct prefixes, no cross-talk.
- `test_slash_composed_live_mqtt_delivery` -- live MQTT delivery to composed prefix.
- `test_slash_composed_command_subtopic_live_mqtt` -- subtopic under composed prefix.
- `test_router_telemetry_prefixed_topic` -- telemetry published to correct prefixed topic.

### Triggerable telemetry under router prefix

`@router.telemetry()` handlers publish to `<prefix>/<name>`.  **Implemented** in
`TestTriggerableTelemetryUnderRouterPrefix` (`test_integration.py`): fires a trigger
and asserts the published topic includes the router prefix.

### Typed payloads

**Implemented** in `test_integration.py`:
- `TestTypedCommandPayload` -- dispatches a JSON payload, asserts Pydantic model
  deserialized correctly and default field populated.
- `TestTypedTelemetryReturn` -- handler returns a Pydantic model, asserts serialised
  JSON published on the expected topic.

### Retry / backoff regression

**Implemented** in `TestRetryBackoff` (`test_cross_feature_integration.py`): simulates
a transient failure on the first attempt, succeeds on the second, and asserts that the
final published state reflects success with no error publish.

### Stream proxy / lifecycle ownership

**Implemented** in `TestStreamProxyLifecycleOwnership` (`test_integration.py`):
asserts the stream runner lifecycle and proxy ownership, and confirms cleanup on
`AppHarness` teardown.

### Periodic / background task archetype

**Implemented** in `TestPeriodicTaskArchetype` (`test_integration.py`): registers a
periodic task, invokes it via `AppHarness.tick_periodic()` N times, and asserts that
side effects accumulate as expected.

---

## Implementation Summary

All phases are complete.

**test_integration.py** -- `TestRouterComposition`, `TestTriggerableTelemetryUnderRouterPrefix`,
`TestTypedCommandPayload`, `TestTypedTelemetryReturn`, `TestPeriodicTaskArchetype`,
`TestStreamProxyLifecycleOwnership`.

**test_cross_feature_integration.py** -- `TestRetryBackoff`.

---

## Acceptance Checklist

- [x] `TestRouterComposition` class present in `test_integration.py` (no-crosstalk, slash-composed live MQTT delivery, slash-composed subtopic, telemetry prefixed topic).
- [x] `TestTriggerableTelemetryUnderRouterPrefix` present in `test_integration.py`.
- [x] `TestTypedCommandPayload` and `TestTypedTelemetryReturn` present in `test_integration.py`.
- [x] Periodic task test present in `test_integration.py` (`TestPeriodicTaskArchetype`) using `AppHarness.tick_periodic`.
- [x] Stream proxy lifecycle test present in `test_integration.py` (`TestStreamProxyLifecycleOwnership`).
- [x] `TestRetryBackoff` present in `test_cross_feature_integration.py`.
- [x] No new broker dependency introduced (all new tests use `AppHarness`).
- [x] `task test:integration` passes with no new skips.
- [x] Coverage matrix rows for all gaps updated to "Covered".

---

## Out of Scope

- Broker-backed variants of the new tests (broker semantics belong in
  `test_mqtt_integration.py` only).
- Performance benchmarks or load tests.
- Documentation updates beyond this planning file.
