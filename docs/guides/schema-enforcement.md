---
icon: material/file-check-outline
---

# Schema Enforcement

cosalette apps publish to predictable MQTT topics by construction — the framework sets
`{prefix}/{device}/state`, `{prefix}/{device}/set`, and `{prefix}/{device}/availability`
automatically. But the payload _shape_ and the _cross-app contract_ (which topics a
fleet expects to exist) are not enforced.

Schema enforcement fills that gap using **AsyncAPI 3.0.0** documents annotated with
`x-cosalette-*` extensions. The key benefits:

- **Catch regressions before deployment.** A renamed field in one app silently
  breaks consumers in another. A schema validation step in your CI/CD pipeline or
  deploy scripts catches it before the app reaches the broker.
- **Machine-readable contract.** Monitoring tools, code generators, and dashboards can
  discover which topics your fleet produces and what payloads to expect.
- **Zero friction when unused.** The default enforcement mode is `off` — no new
  dependencies, no new topics, no broker configuration.

!!! note "Prerequisites"

    Schema features require the `schema` optional extra:

    ```bash
    pip install cosalette[schema]
    ```

    This pulls in `pyyaml` and `jsonschema`. Without it, `mode: off` is the only valid
    enforcement mode and the CLI commands are still available for validation in CI
    environments that have the dependencies installed.

## Quick Start

### 1 — Generate a starter schema from your app

```bash
# Scaffold a schema with x-cosalette extensions included
cosalette schema init --app myapp.app:app > schema.yaml
```

`init` introspects the running app via `app.asyncapi()` and produces a canonical
AsyncAPI 3.0.0 document, then layers on an `x-cosalette-enforcement` scaffold:

```yaml
asyncapi: 3.0.0
info:
  title: thermo2mqtt
  version: 0.1.0
  x-cosalette-contract-version: "1"  # (1)!

x-cosalette-enforcement:             # (2)!
  mode: warn
  on_configure: true
  on_publish: false
  network_level: false

channels:
  temperatureState:
    address: thermo2mqtt/temperature/state
    x-cosalette-app: thermo2mqtt       # (3)!
    x-cosalette-archetype: telemetry
    messages:
      message:
        payload:
          type: object  # (4)!

  setpointCommand:
    address: thermo2mqtt/setpoint/set
    x-cosalette-app: thermo2mqtt
    x-cosalette-archetype: command
    messages:
      message:
        payload:
          type: object

operations:
  publishTemperatureState:
    action: send
    channel:
      $ref: '#/channels/temperatureState'
  receiveSetpointCommand:
    action: receive
    channel:
      $ref: '#/channels/setpointCommand'
```

1. Framework-managed contract-shape version — see [Contract Version Metadata](#contract-version-metadata) below.
2. Enforcement scaffold added by `init` for editing; absent from `dump` output.
3. App-ownership tag emitted from the App registry on every channel — survives
   regeneration, so downstream consumers (e.g. `schema ha-discovery`) resolve the
   owning app without hand-editing.
4. Fallback when no `state_model` or typed return annotation is registered. Add
   properties and constraints by hand, or declare `state_model` on the decorator.
   `@app.telemetry`, `@app.command`, and `@app.device` all accept `state_model=`
   to type their state channels.

!!! tip "`init` vs `dump`"

    Both commands call `app.asyncapi()` under the hood — the output format is
    identical AsyncAPI 3.0.0 with typed payload schemas, archetype and
    app-ownership (`x-cosalette-app`) channel extensions, and
    `x-cosalette-contract-version` in the `info` section.

    - `cosalette schema dump` — outputs the canonical AsyncAPI document. Use this
      to pipe to external tooling (AsyncAPI Studio, documentation generators, or
      for programmatic inspection).
    - `cosalette schema init` — same as `dump`, plus an `x-cosalette-enforcement`
      scaffold at the document root. Use this to create a schema you will commit
      and validate against.

### 2 — Add payload constraints

Edit the generated YAML to specify required fields and types:

```yaml
channels:
  temperatureState:
    address: thermo2mqtt/temperature/state
    x-cosalette-app: thermo2mqtt
    x-cosalette-archetype: telemetry
    messages:
      reading:
        payload:
          type: object
          required: [temperature, unit]
          properties:
            temperature:
              type: number
              x-cosalette-consumer:
                device_class: temperature
                unit: "°C"
                display_name: Room Temperature
                state_class: measurement
            unit:
              type: string
              enum: [celsius, fahrenheit]
```

The `x-cosalette-consumer` annotation carries metadata for downstream consumer code
generation (e.g. home automation integrations, dashboard provisioning). It is
optional — omit it if you do not need consumer code generation.

#### Producing the block: the `consumer()` helper

When you author payloads as pydantic models (rather than hand-writing YAML), attach
the block with the typed `consumer()` producer instead of a hand-built dict:

```python
from typing import Annotated

import pydantic
from cosalette.schema import consumer

class TemperatureReading(pydantic.BaseModel):
    temperature: Annotated[float, pydantic.Field(json_schema_extra=consumer(
        device_class="temperature",
        unit="°C",
        display_name="Room Temperature",
        state_class="measurement",
    ))]
```

`consumer(**meta)` returns `{"x-cosalette-consumer": {...}}` ready to pass to
`Field(json_schema_extra=...)`. Its keyword arguments are typo-checked under a
type checker (ty/pyright) at author time against `ConsumerMeta`, whose key set
(`display_name`, `device_class`, `unit`, `state_class`, `icon`, `read_only`) is
the single source of truth shared with the `ConsumerMetadata` reader. This is a
static check only: at runtime the reader silently ignores unknown keys. The block
rides on the field, so it survives schema
regeneration via `TypeAdapter(model).json_schema()` and feeds the Home Assistant /
OpenHAB discovery generators. See `cosalette ai help consumer`.

#### Semantic presets: `temperature()` and `percent()`

Many fields share a fixed set of metadata keys, differing only in the
`display_name`. `cosalette.schema.temperature()` and `cosalette.schema.percent()`
wrap `consumer()` for the two most common shapes:

```python
from typing import Annotated

import pydantic
from cosalette.schema import percent, temperature

class BoilerState(pydantic.BaseModel):
    flow_temperature: Annotated[
        float, pydantic.Field(json_schema_extra=temperature("Flow Temperature"))
    ]
    modulation: Annotated[
        int, pydantic.Field(json_schema_extra=percent("Modulation"))
    ]
    pump_speed: Annotated[
        int,
        pydantic.Field(
            json_schema_extra=percent("Pump Speed", icon="mdi:pump")
        ),
    ]
```

`temperature(display_name)` returns `consumer(display_name=..., device_class="temperature",
unit="°C", state_class="measurement")`. `percent(display_name, *, icon=None)` returns
`consumer(display_name=..., unit="%", state_class="measurement")`, adding `icon` only
when supplied — omitted, not `None`, when left out — so the output matches a
hand-written block exactly.

#### Platform overrides: `ha_discovery()` and `openhab()`

`x-cosalette-ha-discovery` and `x-cosalette-openhab` (below) have their own typed
producers, mirroring `consumer()`'s pattern. Because a field usually needs
`consumer()` plus one or both override producers, combine them with `merge()` —
`Field(json_schema_extra=...)` accepts only one dict:

```python
from typing import Annotated

import pydantic
from cosalette.schema import consumer, ha_discovery, merge, openhab

class BulbState(pydantic.BaseModel):
    hsb: Annotated[
        list[int],
        pydantic.Field(json_schema_extra=merge(
            consumer(display_name="HSB"),
            openhab(
                item_type="Color",
                channel_type="color",
                channel_params={"colorMode": "HSB"},
            ),
        )),
    ]
    state: Annotated[
        bool,
        pydantic.Field(json_schema_extra=merge(
            consumer(display_name="Desk Lamp"),
            ha_discovery(extra={"schema": "json", "optimistic": False}),
        )),
    ]
```

`ha_discovery(**meta)` and `openhab(**meta)` are typo-checked against
`HaDiscoveryMeta` / `OpenHabMeta`, the single source of truth shared with the
`HaDiscoveryOverrides` / `OpenHabOverrides` readers. Each also carries one open,
untyped passthrough field — `extra` on `ha_discovery()`, `channel_params` on
`openhab()` — for platform keys the curated fields don't reach; both are merged
in last, so they can add a new key or override a computed default (e.g. the
`switch` channel's default `on`/`off`). `merge()` raises `ValueError` if two
blocks carry the same extension key. See `cosalette ai help consumer-overrides`
and ADR-056.

### 3 — Validate the schema document

```bash
cosalette schema validate schema.yaml
```

```text
✅ Schema validated: thermo2mqtt v0.1.0
   AsyncAPI version: 3.0.0
   Channels: 2
   Schema type: single-app
```

This checks structure and cosalette-specific extension syntax. It does **not** run the
app — it validates the schema file alone.

### 4 — Check app registrations against the schema

```bash
cosalette schema check --app thermo2mqtt.app:app --schema schema.yaml
```

```text
Schema: schema.yaml (v0.1.0)
App:    thermo2mqtt

✓ temperature — OK
✓ setpoint — OK

Result: 0 violations, 2 compliant
Exit code: 0
```

If a device is missing or a scope rule is violated, `check` exits with code 1 and
prints the violation:

```text
✗ setpoint — MISSING
    Schema expects device 'setpoint' but no registration found

Result: 1 violations, 1 compliant
Exit code: 1
```

### 5 — Enable enforcement in the app

Schema enforcement is configured through the framework's nested settings model under
the `schema` key. The two relevant fields are:

| Settings field | Env var | Description |
|---|---|---|
| `schema.path` | `SCHEMA__PATH` | Path to the AsyncAPI schema file. |
| `schema.enforcement` | `SCHEMA__ENFORCEMENT` | Runtime mode: `off`, `warn`, or `strict`. |

Set them in your `.env` file (or environment):

```env
SCHEMA__PATH=/etc/cosalette/thermo2mqtt-schema.yaml
SCHEMA__ENFORCEMENT=warn
```

Or in a settings subclass if you prefer code-level defaults:

```python
from cosalette import App, Settings
from cosalette._settings import SchemaSettings

class MySettings(Settings):
    schema_: SchemaSettings = SchemaSettings(
        path="/etc/cosalette/thermo2mqtt-schema.yaml",
        enforcement="warn",
    )

app = App(name="thermo2mqtt", settings_class=MySettings)
```

!!! note
    The `x-cosalette-enforcement` block inside the schema file is treated as **metadata**
    (used by `cosalette schema validate` and `check` commands). The runtime enforcement
    mode is always set through `Settings`, not read from the YAML at startup.

---

## Enforcement Modes

| Mode | Behaviour |
|------|-----------|
| `off` | No validation. Zero dependencies required. Default. |
| `warn` | Log violations, continue running. Safe for production. |
| `strict` | Fail startup on violation. Use in CI and staging. |

Configure in the schema file:

```yaml
x-cosalette-enforcement:
  mode: warn          # off | warn | strict
  on_configure: true  # validate device registrations at startup
  on_publish: false   # validate payload shape at publish time (dev only)
  network_level: false
```

!!! tip "Recommended progression"

    1. Start with `mode: warn` to discover violations without breaking production.
    2. Move to `mode: strict` in CI (`schema check` exit code 1 fails the deploy).
    3. Enable `on_publish: true` in a dev/staging environment to catch payload errors
       during testing.

---

## Network-Level Schema

For a fleet of multiple cosalette apps, a **network-level schema** defines the entire
MQTT topology in one file. Each app validates against its own slice.

### When to use it

A network schema is the primary use case for cross-app validation:

- One app renames a topic → the network schema flags it; the deploy is blocked before
  the change reaches the broker.
- An app adds a new channel → `cosalette schema check` reports it as "extra" (not a
  violation) so you can decide whether to promote it to the schema.
- A CI/CD gate validates all apps in a single step before any of them are deployed.

### Network schema structure

```yaml
asyncapi: 3.0.0
info:
  title: My MQTT Network
  version: 1.0.0

x-cosalette-enforcement:
  mode: warn
  network_level: true   # marks this as a network-level schema

channels:
  thermoTemperatureState:
    address: thermo2mqtt/temperature/state
    x-cosalette-app: thermo2mqtt           # channel belongs to this app
    x-cosalette-archetype: telemetry
    messages:
      reading:
        payload:
          type: object
          required: [temperature]
          properties:
            temperature:
              type: number

  airsenseAirQualityState:
    address: airsense2mqtt/airquality/state
    x-cosalette-app: airsense2mqtt
    x-cosalette-archetype: telemetry
    messages:
      reading:
        payload:
          type: object
          required: [co2, humidity]
          properties:
            co2:
              type: integer
            humidity:
              type: number

```

### Extract an app's slice

The `slice` command filters a network schema to a single app's channels:

```bash
cosalette schema slice --network /etc/cosalette/network-schema.yaml --app thermo2mqtt
```

```yaml
asyncapi: 3.0.0
info:
  title: thermo2mqtt
  version: 2.0.0

x-cosalette-enforcement:
  mode: warn
  on_configure: true
  on_publish: false
  network_level: false

channels:
  thermoTemperatureState:
    address: thermo2mqtt/temperature/state
    x-cosalette-app: thermo2mqtt
    x-cosalette-archetype: telemetry
    ...
```

You can pipe this directly to a file or use it for per-app validation:

```bash
cosalette schema slice \
  --network /etc/cosalette/network-schema.yaml \
  --app thermo2mqtt > /etc/cosalette/thermo2mqtt-schema.yaml
```

### Check against a network schema

`check` automatically filters the network schema to the app's slice:

```bash
cosalette schema check \
  --app thermo2mqtt.app:app \
  --schema /etc/cosalette/network-schema.yaml
```

The command detects `network_level: true`, extracts the `thermo2mqtt` slice, and
validates the app's registrations against it. No separate `slice` step needed.

### Reference schema

See
[`docs/assets/reference-network-schema.yaml`](../assets/reference-network-schema.yaml)
for a complete annotated example covering a three-app smart-home fleet
(`thermo2mqtt`, `airsense2mqtt`, `solarray2mqtt`) with telemetry, command, and fleet-wide
availability channels.

---

## Deployment Integration

`cosalette schema check` is a standard subprocess that exits **0** on compliance and
**1** on violations, so it integrates cleanly into any deploy toolchain.

### Shell / CI scripts

The simplest form — run before starting each app:

```bash
cosalette schema check \
  --app myapp.app:app \
  --schema /etc/cosalette/network-schema.yaml || exit 1

# Start the app only if validation passed
myapp start
```

This works in any environment: bare-metal init scripts, Docker entrypoints, GitHub
Actions steps, GitLab CI jobs, or Makefile targets.

### GitHub Actions example

```yaml
- name: Validate schema
  run: |
    cosalette schema check \
      --app myapp.app:app \
      --schema schemas/network-schema.yaml
```

### Ansible example

For teams using Ansible to manage hosts, the pattern below deploys the schema file
first and validates before starting the service:

```yaml
# tasks/deploy-myapp.yml

- name: Deploy network schema
  ansible.builtin.copy:
    src: files/network-schema.yaml
    dest: /etc/cosalette/network-schema.yaml
    mode: "0644"

- name: Validate myapp against network schema
  ansible.builtin.command:
    cmd: >
      cosalette schema check
        --app myapp.app:app
        --schema /etc/cosalette/network-schema.yaml
  changed_when: false
  failed_when: result.rc != 0
  register: result

- name: Start myapp service
  ansible.builtin.systemd:
    name: myapp
    state: started
  when: result.rc == 0
```

The exit-code contract is the same regardless of toolchain — `check` failing blocks the
next step.

---

## x-cosalette Extension Reference

### Channel-level extensions

| Extension | Type | Description |
|-----------|------|-------------|
| `x-cosalette-app` | `string` | App name that owns this channel. Emitted automatically on every channel by `app.asyncapi()` (and therefore `schema dump` / `schema init`) from the App registry, so it survives regeneration. Consumers resolve the owning app via this tag (e.g. `schema ha-discovery`); required for network schemas. |
| `x-cosalette-archetype` | `string` | One of `device`, `telemetry`, `command`, `stream`. |
| `x-cosalette-scope` | `string` | `all_apps` — channel is shared across all apps (e.g. availability). |
| `x-cosalette-coalescing-group` | `string` | [Coalescing group](../concepts/telemetry.md#coalescing-groups) this channel belongs to. |
| `x-cosalette-requires` | `list` | Capability tag requirements (see ADR-014). |
| `x-cosalette-summary` | `string` | Human-readable summary of the channel's purpose. Emitted when a `summary=` argument is supplied to the decorator. |
| `x-cosalette-behavior` | `list` | Behavioral properties of the channel (e.g. ordering guarantees, idempotency). Emitted when a `behavior=` argument is supplied to the decorator. |
| `x-cosalette-effects` | `list` | Side effects produced when a message is received on this channel. Emitted when an `effects=` argument is supplied to the decorator. |

### Payload-level extensions (whole payload model)

| Extension | Type | Description |
|-----------|------|-------------|
| `x-cosalette-ha-discovery.entities` | `list` | Composite Home Assistant entities spanning the whole payload model, produced by `ha_entities(ha_entity(...))` on the model's `pydantic.ConfigDict(json_schema_extra=...)` — not a field. See [Composite entities](#composite-entities) below. |

### Property-level extensions

| Extension | Type | Description |
|-----------|------|-------------|
| `x-cosalette-consumer` | `object` | Consumer metadata for downstream integration code generation (home automation, dashboards, etc.). |
| `x-cosalette-consumer.device_class` | `string` | Semantic device class consumed by integrations (e.g. `temperature`, `battery`). |
| `x-cosalette-consumer.unit` | `string` | Unit string for display in consumer integrations. |
| `x-cosalette-consumer.display_name` | `string` | Human-readable name for the property. |
| `x-cosalette-consumer.state_class` | `string` | `measurement`, `total`, `total_increasing`. |
| `x-cosalette-ha-discovery` | `object` | Home Assistant MQTT discovery overrides. |
| `x-cosalette-ha-discovery.component` | `string` | HA component type override (e.g. `sensor`, `binary_sensor`, `switch`). Auto-inferred from archetype + JSON type when absent. |
| `x-cosalette-ha-discovery.value_template` | `string` | Jinja2 value template. Default: `{{ value_json.<name> }}`. |
| `x-cosalette-ha-discovery.command_template` | `string` | Jinja2 command template for command channels. |
| `x-cosalette-ha-discovery.expire_after` | `integer` | Seconds after which HA marks the entity unavailable. |
| `x-cosalette-ha-discovery.extra` | `object` | Open passthrough for HA platform keys the curated fields don't cover (e.g. `schema`, `optimistic`, `supported_color_modes`). Merged into the payload last, so it can also override a curated key. |
| `x-cosalette-openhab` | `object` | OpenHAB configuration overrides. |
| `x-cosalette-openhab.item_type` | `string` | OpenHAB item type override (e.g. `Number:Temperature`, `Dimmer`). |
| `x-cosalette-openhab.label` | `string` | Display label override. |
| `x-cosalette-openhab.groups` | `list` | OpenHAB group memberships. |
| `x-cosalette-openhab.tags` | `list` | OpenHAB semantic tags (e.g. `Measurement`, `Temperature`). |
| `x-cosalette-openhab.channel_type` | `string` | OpenHAB `.things` channel type override (e.g. `color`, `dimmer`). Auto-inferred from JSON type when absent. |
| `x-cosalette-openhab.channel_params` | `object` | Open passthrough for `.things` channel parameters (e.g. `colorMode`, `min`/`max`/`step`). Merged into the channel last, so it can also override a computed parameter such as `on`/`off`. |

### Document-level enforcement config

```yaml
x-cosalette-enforcement:
  mode: warn          # off | warn | strict
  on_configure: true  # validate at startup
  on_publish: false   # validate payload at publish time
  network_level: false
```

### Info-level metadata

| Extension | Type | Description |
|-----------|------|-------------|
| `x-cosalette-contract-version` | `string` | Contract-shape version managed by the framework. Present in every document produced by `app.asyncapi()`. |

---

## Contract Version Metadata

`x-cosalette-contract-version` appears in the `info` section of every AsyncAPI
document generated by `app.asyncapi()` (and therefore by `cosalette schema dump`,
`cosalette schema init`, `cosalette manifest`, and the `cosalette_manifest` MCP tool):

```yaml
info:
  title: myapp
  version: 1.2.0
  x-cosalette-contract-version: "1"
```

This value tracks the **shape of the generated document** independently from the
application version. Its semantics:

| Scenario | Action |
|----------|--------|
| Application version bumped | `info.version` changes; `x-cosalette-contract-version` unchanged. |
| cosalette releases a breaking change to the AsyncAPI output structure (new required top-level key, renamed channel fields, etc.) | `x-cosalette-contract-version` is bumped. |
| New optional extensions added (e.g. a new `x-cosalette-*` field) | No bump — backwards compatible. |

**Migration guidance:** When `x-cosalette-contract-version` increases after a
cosalette upgrade, re-run `cosalette schema dump` to regenerate your committed
schema baseline. The [CHANGELOG](https://github.com/ff-fab/cosalette/blob/main/CHANGELOG.md) documents every bump with a
description of the structural change. Hand-maintained schema files (`schema.yaml`)
are not affected — they are only validated against the running app, not regenerated
automatically.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `cosalette schema validate <file>` | Validate schema document structure. |
| `cosalette schema check --app module:attr --schema <file>` | Check app registrations against schema (CI gate). |
| `cosalette schema dump --app module:attr` | Generate canonical AsyncAPI 3.0.0 YAML via `app.asyncapi()` (typed schemas, archetype extensions, contract-version). |
| `cosalette schema init --app module:attr` | Generate starter schema with cosalette extensions (for editing). |
| `cosalette schema slice --network <file> --app <name>` | Extract one app's slice from a network schema. |
| `cosalette schema ha-discovery <file> [--prefix PREFIX] [--format json\|yaml]` | Generate Home Assistant MQTT discovery payloads. |
| `cosalette schema openhab <file> [--broker-uid UID] [--output things\|items\|both]` | Generate OpenHAB `.things` / `.items` configuration. |
| `cosalette schema acl <file> [--format FORMAT]` | Generate broker ACL configuration. |
| `cosalette schema monitor <file> [--broker HOST:PORT] [--timeout SECS]` | Monitor fleet schema compliance via MQTT. |

---

## Consumer Code Generation

Properties annotated with `x-cosalette-consumer` can be transformed into
consumer platform configurations automatically.  This eliminates
hand-maintaining discovery payloads and configuration files — the AsyncAPI
schema becomes the single source of truth.

### Home Assistant MQTT Discovery

Generate HA discovery payloads that Home Assistant accepts via its MQTT
discovery protocol:

```bash
cosalette schema ha-discovery network.yaml
```

Output is a JSON array of `{topic, config}` objects — one per annotated
property.  Each object contains the discovery topic and the full config
payload.  Publish these as retained messages and HA will auto-create entities.

**Component inference:** When `x-cosalette-ha-discovery.component` is not set,
the component is inferred from archetype and JSON schema type:

| Archetype | JSON Type | Component |
|-----------|-----------|---------------|
| telemetry | number | `sensor` |
| telemetry | boolean | `binary_sensor` |
| command | boolean | `switch` |
| command | integer / number | `number` |
| command | string + enum | `select` |
| command | string | `text` |

Optional fields (`int | None`, emitted as `anyOf: [T, null]`) infer from their
non-null variant `T` rather than degrading to `sensor`/`string`.

**Command entities publish a JSON envelope.** cosalette's wire format is one
JSON object per channel, so a command entity gets a `command_template` of
`{"<prop>": {{ value }}}` (string properties are quoted) unless you supply an
explicit `command_template`.  Command entities also carry a `_cmd` suffix in
their `object_id` / `unique_id` / discovery topic so a state entity and a
command entity for the same device and property never collide.  State entities
keep their bare identifiers, so existing read-only apps are unaffected.

**Schema constraints are honoured.** `number` entities emit `min` / `max` /
`step` from `minimum` / `maximum` / `multipleOf`; `select` entities emit
`options` from `enum`; array properties render via a `join` filter
(`{{ value_json.rgb | join(',') }}`).  Keys the target platform rejects are
dropped — a `binary_sensor` does not receive `unit_of_measurement` or
`state_class`.

**`read_only`** forces a read-only component (`binary_sensor` / `sensor`) and
emits a `state_topic` only, never a `command_topic`.

### Composite entities

The overrides above still produce **one HA entity per property**. For a single
entity spanning several JSON fields — a `light` with `state` + `brightness` +
`color_temp`, a `climate`, a `cover` with position — declare it on the payload
**model**, not a field, with `ha_entities(ha_entity(...))`:

```python
from typing import Annotated
import pydantic
from cosalette.schema import consumer, ha_entities, ha_entity


class BulbState(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        json_schema_extra=ha_entities(
            ha_entity(
                component="light",
                name="Desk Lamp",
                extra={
                    "schema": "json",
                    "brightness": True,
                    "supported_color_modes": ["color_temp", "hs"],
                },
            ),
        )
    )

    state: Annotated[bool, pydantic.Field(json_schema_extra=consumer())]
    brightness: Annotated[int, pydantic.Field(json_schema_extra=consumer())]
```

A channel whose payload model declares `ha_entities` skips per-property scalar
generation **entirely** for that channel — the composite entity replaces the
scatter, it does not add to it.

`component` selects a real payload builder, not just a topic segment:

| Component | Builder default |
|-----------|------------------|
| `light` | `schema: "json"` — HA's MQTT JSON light schema reads/writes the retained body directly, matching cosalette's own wire format. |
| `climate` | Drops the generic `state_topic` / `command_topic` — HA's MQTT climate has no single one; every capability needs its own `<x>_state_topic` / `<x>_command_topic` pair, supplied via `extra`. |
| `cover` | Keeps the inherited `state_topic` / `command_topic` — a plain open/close/stop cover accepts them natively. |
| anything else | No extra defaults; `state_topic` / `command_topic` are set from channel direction as usual. |

`extra` is merged last, same override-last semantics as `ha_discovery().extra`.

A `device` archetype channel with `payload_model=` emits a paired `/state`
(send) and `/set` (receive) channel sharing one model — the entity's two topic
halves are merged into one config automatically rather than emitted twice,
each incomplete.

See ADR-057 for the full design rationale.

**Example:**

```yaml
# In your AsyncAPI schema
temperature:
  type: number
  x-cosalette-consumer:
    device_class: temperature
    unit: '°C'
    display_name: 'Heating Water Temperature'
    state_class: measurement
  x-cosalette-ha-discovery:
    expire_after: 300
```

Produces a discovery payload at
`homeassistant/sensor/<app>/<device>_temperature/config` with `device_class`,
`unit_of_measurement`, `state_class`, `value_template`, and `expire_after`
fields set correctly.

### OpenHAB Configuration

Generate OpenHAB `.things` and `.items` files:

```bash
cosalette schema openhab network.yaml --output both
cosalette schema openhab network.yaml --output things
cosalette schema openhab network.yaml --output items
```

Things use JSONPATH transformations to extract individual properties from JSON
payloads.  Items are typed according to `device_class` (e.g.
`Number:Temperature`) or explicit `x-cosalette-openhab.item_type` overrides.

Each resolved device produces **one** Thing (the union of its state and command
channels) rather than one Thing per channel, so a bidirectional device no longer
emits duplicate Thing UIDs.  Command channels and their Items carry a `_cmd`
suffix so they never collide with the state side, and both sides derive their
names from the same helper so an Item always links to the channel it names.
Command channels build an outbound JSON envelope with `formatBeforePublish`
(`{"<prop>":%s}`) instead of an inbound-only `transformationPattern`, and
boolean `switch` channels emit `on="true"` / `off="false"` so JSON booleans are
not left `UNDEF`.  Nested device addresses (`{app}/{room}/{device}/state`)
resolve the device to `{room}/{device}`.

---

## Further Reading

- [ADR-033 — MQTT Schema Enforcement](../adr/ADR-033-mqtt-schema-enforcement.md) —
  decision record: format choice, distribution model, enforcement modes.
- [Reference Network Schema](../assets/reference-network-schema.yaml) — annotated
  three-app fleet schema.
