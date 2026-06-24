# Framework Improvement Proposals

These proposals capture framework gaps identified during development of wallpanel-control.
Each documents a real limitation encountered in production code, the workaround used,
and a proposed framework improvement.

---

## Resolved

### R1. Typed Command Payload and State Models

**Context:** wallpanel-control needs strict validation of inbound command JSON and
outbound state JSON. Without typed models, payload parsing and validation are manual.

**Resolution (cosalette 0.4):** `@router.command(payload_model=..., state_model=...)`
accepts Pydantic model classes. The framework validates inbound JSON against the payload
model and rejects unknown fields automatically. State publications use the state model's
`.model_dump()` output. wallpanel-control uses this for both `display` and
`system/action` command handlers.

```python
@router.command("display", payload_model=DisplayCommand, state_model=DisplayState)
async def handle_display(
    cmd: Annotated[DisplayCommand, Payload()],
    wallpanel: WallpanelPort,
    state: _DisplayHandlerState,
) -> DisplayState:
    ...
    return DisplayState(...)
```

The framework publishes the returned state model automatically via `state_model`.

**Impact:** Eliminates hand-written validation. Schema errors are published to the error
topic automatically.

---

### R2. Retained State via `publish_state`

**Context:** State payloads should be retained so that new MQTT subscribers immediately
see the last-known state.

**Resolution (cosalette 0.4):** `ctx.publish_state()` uses cosalette's `publish_state`
helper which defaults to QoS 1 and retained. wallpanel-control relies on this for both
`display/state` and `system/action/state` without any per-call configuration.

---

### R3. Shared App State

**Context:** Earlier wallpanel-control designs considered shared state between display
and status handlers. Without framework-level state, that would have required module
globals or adapter-owned caches.

**Resolution (cosalette 0.4):** Applications can register shared state with `@app.state`
and inject it into handlers. The final wallpanel-control API no longer needs a global
status cache because status polling was removed, but the framework capability is now
available for apps that do need shared runtime state.

---

## Remaining

### 1. SSH Unreachability as Availability State

**Context:** When SSH to the wall panel fails, the display handler publishes
`{"available": false, "state": null, "brightness_percent": null}` manually. This
"unavailable state payload" pattern is repeated across any app that wraps a fallible
transport (SSH, BLE, serial).

**Current behavior:** Each app handler checks the exception type and manually
constructs an unavailable state payload. There is no framework convention or helper
for this.

**Workaround:** wallpanel-control catches `WallpanelUnreachableError` in the display
handler, constructs `DisplayState(available=False, state=None,
brightness_percent=None)`, and returns it for publication.

**Proposed improvement:** A first-class `availability` flag on `@router.command` or
a dedicated `ctx.mark_unavailable()` helper that publishes a framework-managed
availability payload and suppresses further state publications until the transport
recovers:

```python
@router.command(
    "display",
    payload_model=DisplayCommand,
    state_model=DisplayState,
    unavailable_on=(SSHError,),
)
async def handle_display(...) -> None:
    ...
```

**Impact:** Standardises unavailability signaling. Enables Home Assistant MQTT
integration to consume a standard `availability` topic instead of inspecting the
`available` field inside each state payload.

**Apps affected:** wallpanel-control (SSH), airthings2mqtt (BLE), vito2mqtt (serial).

---

### 2. First-Class Command Acknowledgement / Observed-State Semantics

**Context:** `system/action/state` is currently an optimistic acknowledgement
(`{"accepted": true, "action": "suspend"}`). The framework has no built-in concept
of "command received + action attempted" vs. "observed state changed". wallpanel-control
conflates both in a single state model.

**Current behavior:** The handler publishes `accepted: true` immediately after the SSH
call succeeds, without waiting for observable state change. For `wake`, there is no way
to confirm the panel actually started.

**Workaround:** Document the limitation in the MQTT topics reference. Consumers that
need confirmation must poll a separate sensor (e.g. ping) outside wallpanel-control.

**Proposed improvement:** A command-acknowledgement channel separate from observed state:

```python
@router.command("system/action", payload_model=SystemActionCommand)
async def handle_action(cmd: SystemActionCommand, ctx: DeviceContext) -> None:
    await ctx.ack(cmd, accepted=True)   # framework-managed ack topic
    await ctx.publish_state(observed_state)  # framework-managed state topic
```

**Impact:** Clean separation between "command was dispatched" and "state was observed".
Enables richer Home Assistant button/switch integrations.

**Apps affected:** wallpanel-control (system actions), velux2mqtt (cover commands).

---

### 3. Harness Helpers for Publish/Subscription Assertions

**Context:** wallpanel-control tests verify MQTT publish calls using
`AppHarness` + `harness.mqtt.get_messages_for()` from `cosalette.testing`.
Unit tests exercise handler functions directly via `FakeWallpanel`.
As the topic and payload schema grows, assertion boilerplate grows proportionally.

**Current behavior:** Integration tests use `AppHarness` (which manages the full
cosalette event loop) and `MockMqttClient.get_messages_for(topic)` to retrieve
published payloads. Subscription assertions are manual `in harness.mqtt.subscriptions`
checks. There are no built-in matchers for partial payload comparison or topic patterns.

**Workaround:** Test helpers decode JSON payloads from the mock before asserting field
by field. This is functional but adds noise when verifying only a subset of fields.

**Proposed improvement:** A `cosalette.testing` harness with subscription/publish
matchers:

```python
from cosalette.testing import MqttHarness

async def test_display_on(harness: MqttHarness) -> None:
    await harness.publish("wallpanel-control/display/set", {"state": "on"})
    harness.assert_state("wallpanel-control/display/state", {"available": True, "state": "on"})
```

**Impact:** Reduces test boilerplate. Standardises MQTT interaction testing across all
cosalette apps.

**Apps affected:** All apps with command handlers.
