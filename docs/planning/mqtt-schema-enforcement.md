# MQTT Schema Enforcement

**Date:** 2026-04-08
**Epic:** COS-5hx — MQTT Schema Enforcement
**Phase:** 1 — Evaluation and Use-Case Analysis

---

## 1. Problem Statement

cosalette enforces MQTT topic conventions by code (ADR-002) and framework behaviour:
devices automatically publish to `{app}/{device}/state`, subscribe to
`{app}/{device}/set`, and announce availability on `{app}/{device}/availability`. App-level
topics (`{app}/status`, `{app}/error`) are wired by the runtime. The _structure_ is
correct by construction, but the _content_ is not verified:

1. **No payload shape enforcement.** A telemetry handler returning
   `{"temp": 22.5}` today and `{"temperature": 22.5}` tomorrow will not be caught —
   downstream consumers silently break.

2. **No capability-based requirements.** There is no way to declare "every device tagged
   `battery_powered` must publish a `/battery` topic with `{level: int, charging: bool}`."
   Capability contracts live only in developer memory.

3. **No machine-readable contract.** Monitoring tools, code generators, and documentation
   systems cannot discover which topics an app produces or consumes — only
   `build_registry_snapshot()` provides runtime introspection, and it carries none of the
   payload schema information.

4. **No validation mode for development.** Typos, missing fields, and type mismatches in
   payloads surface only when an MQTT consumer fails. A dev-time strict mode that
   validates outgoing payloads before publishing would shorten the feedback loop
   dramatically.

This planning document evaluates schema format options, maps them to cosalette's
architecture, and defines concrete use cases for an enforcement layer.

---

## 2. Schema Format Evaluation

### 2.1 AsyncAPI Deep-Dive Evaluation (COS-5hx.1)

#### Channel/Message Model Mapping to cosalette

cosalette's MQTT topic convention (ADR-002) defines a flat, Home Assistant-aligned
layout:

| Topic Pattern                              | Direction   | Retained | Purpose                     |
| ------------------------------------------ | ----------- | -------- | --------------------------- |
| `{app}/{device}/state`                     | publish     | yes      | Device state (JSON)         |
| `{app}/{device}/set`                       | subscribe   | no       | Command input               |
| `{app}/{device}/set/{subtopic}`            | subscribe   | no       | Sub-topic commands (ADR-025)|
| `{app}/{device}/availability`              | publish     | yes      | Device online/offline (LWT) |
| `{app}/status`                             | publish     | yes      | App-level health (LWT)      |
| `{app}/error`                              | publish     | no       | Structured error events     |
| `{app}/{device}/error`                     | publish     | no       | Device-level errors         |

AsyncAPI 3.0.0 maps these with **Channel Address Expressions** — parametrised URI
templates on the `address` field:

```yaml
channels:
  deviceState:
    address: "{appName}/{deviceName}/state"
    parameters:
      appName:
        description: The application name (e.g. vito2mqtt)
      deviceName:
        description: The device name within the app
    messages:
      stateMessage:
        $ref: "#/components/messages/DeviceState"

  deviceSet:
    address: "{appName}/{deviceName}/set"
    parameters:
      appName:
        $ref: "#/channels/deviceState/parameters/appName"
      deviceName:
        $ref: "#/channels/deviceState/parameters/deviceName"
    messages:
      commandMessage:
        $ref: "#/components/messages/DeviceCommand"

  deviceSetSubtopic:
    address: "{appName}/{deviceName}/set/{subtopic}"
    parameters:
      appName:
        $ref: "#/channels/deviceState/parameters/appName"
      deviceName:
        $ref: "#/channels/deviceState/parameters/deviceName"
      subtopic:
        description: Sub-topic for routed commands (ADR-025)
    messages:
      subtopicCommand:
        $ref: "#/components/messages/DeviceCommand"
```

**Archetype-to-operation mapping:**

| cosalette Archetype                | AsyncAPI Operation | Channel(s)                           |
| ---------------------------------- | ------------------ | ------------------------------------ |
| `@app.device(name)` (bidirectional)| `send` + `receive` | `deviceState` (send), `deviceSet` (receive) |
| `@app.telemetry(name, interval=)`  | `send` only        | `deviceState` (send)                 |
| `@app.command(name)`               | `receive` only     | `deviceSet` (receive)                |
| Sub-topic command (ADR-025)        | `receive`          | `deviceSetSubtopic` (receive)        |

Per ADR-019, a telemetry and command registration sharing the same name produce a
single device identity with both `send` on `/state` and `receive` on `/set` — this
maps to two AsyncAPI operations referencing the same `deviceName` parameter value.

#### Key AsyncAPI 3.0.0 Features

**Channels Object.** Each channel has a first-class `address` field with Channel
Address Expressions (e.g. `{appName}/{deviceName}/state`). This is a 3.0 improvement
over 2.x, where channels were keyed by their address string.

**Parameters Object.** Parameters support `enum` (constraining valid device names to a
known set), `default`, and `description`. For cosalette, `enum` could list all known
device names per app, turning the schema into a complete topic inventory:

```yaml
parameters:
  deviceName:
    enum: [temperature, valve, hot_water]
    description: Registered device names for vito2mqtt
```

**Messages Object.** Each message carries a `payload` key that accepts JSON Schema
(Draft 07 superset). This is the mechanism for type-safe payload enforcement.

**Operations.** 3.0.0 separates operations from channels. An operation has
`action: send | receive`, references one or more channels, and can carry its own
bindings. This cleanly models cosalette's archetype distinction: telemetry → `send`,
command → `receive`, device → both.

**MQTT Bindings.** The AsyncAPI MQTT Binding specification defines operation-level
properties critical for cosalette:

- `qos`: 0, 1, or 2 — cosalette uses QoS 1 for state and availability.
- `retain`: `true` or `false` — state and availability are retained (ADR-002); errors
  are not.
- `messageExpiryInterval`, `payloadFormatIndicator`, `contentType` for MQTT 5.

```yaml
operations:
  publishTemperatureState:
    action: send
    channel:
      $ref: "#/channels/deviceState"
    bindings:
      mqtt:
        qos: 1
        retain: true
```

**Message Traits.** Shared properties across messages — e.g., all state messages
include a common timestamp header or content-type declaration. Avoids duplication
across per-device message definitions.

**Operation Traits.** Shared operation-level properties — e.g., all publish operations
use QoS 1 + retain. Reduces boilerplate when many devices share the same MQTT settings.

**Components.** Reusable definitions for schemas, messages, channels, operations,
traits, and parameters. A cosalette AsyncAPI document would define base schemas in
`components/schemas` and reference them per device.

#### Concrete Example: vito2mqtt

```yaml
asyncapi: 3.0.0
info:
  title: vito2mqtt
  version: 0.2.0
  description: Viessmann Vitoconnect to MQTT bridge (cosalette app)

defaultContentType: application/json

servers:
  production:
    host: mqtt.local:1883
    protocol: mqtt
    bindings:
      mqtt:
        cleanSession: true
        keepAlive: 60

channels:
  temperatureState:
    address: vito2mqtt/temperature/state
    messages:
      temperatureReading:
        $ref: "#/components/messages/TemperatureState"

  valveState:
    address: vito2mqtt/valve/state
    messages:
      valveReading:
        $ref: "#/components/messages/ValveState"

  valveSet:
    address: vito2mqtt/valve/set
    messages:
      valveCommand:
        $ref: "#/components/messages/ValveCommand"

  appStatus:
    address: vito2mqtt/status
    messages:
      statusMessage:
        $ref: "#/components/messages/AppStatus"

  appError:
    address: vito2mqtt/error
    messages:
      errorMessage:
        $ref: "#/components/messages/AppError"

  deviceAvailability:
    address: "vito2mqtt/{deviceName}/availability"
    parameters:
      deviceName:
        enum: [temperature, valve]
    messages:
      availabilityMessage:
        $ref: "#/components/messages/DeviceAvailability"

operations:
  publishTemperature:
    action: send
    channel:
      $ref: "#/channels/temperatureState"
    bindings:
      mqtt:
        qos: 1
        retain: true

  publishValveState:
    action: send
    channel:
      $ref: "#/channels/valveState"
    bindings:
      mqtt:
        qos: 1
        retain: true

  receiveValveCommand:
    action: receive
    channel:
      $ref: "#/channels/valveSet"
    bindings:
      mqtt:
        qos: 1
        retain: false

  publishStatus:
    action: send
    channel:
      $ref: "#/channels/appStatus"
    bindings:
      mqtt:
        qos: 1
        retain: true

  publishError:
    action: send
    channel:
      $ref: "#/channels/appError"
    bindings:
      mqtt:
        qos: 0
        retain: false

  publishAvailability:
    action: send
    channel:
      $ref: "#/channels/deviceAvailability"
    bindings:
      mqtt:
        qos: 1
        retain: true

components:
  messages:
    TemperatureState:
      payload:
        $ref: "#/components/schemas/TemperaturePayload"
      traits:
        - $ref: "#/components/messageTraits/retainedState"

    ValveState:
      payload:
        $ref: "#/components/schemas/ValvePayload"
      traits:
        - $ref: "#/components/messageTraits/retainedState"

    ValveCommand:
      payload:
        $ref: "#/components/schemas/ValveCommandPayload"

    AppStatus:
      payload:
        $ref: "#/components/schemas/StatusPayload"

    AppError:
      payload:
        $ref: "#/components/schemas/ErrorPayload"

    DeviceAvailability:
      payload:
        $ref: "#/components/schemas/AvailabilityPayload"

  schemas:
    TemperaturePayload:
      type: object
      required: [temperature, unit]
      properties:
        temperature:
          type: number
          description: Current temperature reading
        unit:
          type: string
          enum: [celsius, fahrenheit]
      additionalProperties: false

    ValvePayload:
      type: object
      required: [position]
      properties:
        position:
          type: integer
          minimum: 0
          maximum: 100
          description: Valve position as percentage
      additionalProperties: false

    ValveCommandPayload:
      type: object
      required: [position]
      properties:
        position:
          type: integer
          minimum: 0
          maximum: 100

    StatusPayload:
      type: object
      required: [online]
      properties:
        online:
          type: boolean

    ErrorPayload:
      type: object
      required: [error, timestamp]
      properties:
        error:
          type: string
        timestamp:
          type: string
          format: date-time
        device:
          type: string

    AvailabilityPayload:
      type: object
      required: [online]
      properties:
        online:
          type: boolean

  messageTraits:
    retainedState:
      description: Common trait for all retained state messages
      contentType: application/json
```

#### Conditional Requirements Assessment

AsyncAPI defines topic structure and payload shapes — it does **not** provide a
mechanism for conditional requirements like "if a device has tag `battery_powered`,
it must publish on channel `{app}/{device}/battery`." This is a fundamental gap for
cosalette's capability-based model.

**Option 1: Extension properties (`x-cosalette-*`).**
AsyncAPI allows vendor extensions on most objects. A custom `x-cosalette-requires`
field on a channel could express "this channel is required when the device has tag X":

```yaml
channels:
  batteryState:
    address: "{appName}/{deviceName}/battery"
    x-cosalette-requires:
      tag: battery_powered
    messages:
      batteryReading:
        $ref: "#/components/messages/BatteryState"
```

This keeps everything in one file but requires a custom validator — standard AsyncAPI
tooling ignores `x-` properties.

**Option 2: External manifest alongside AsyncAPI.**
A separate YAML file lists capability tags and their required channels, referencing
the AsyncAPI document for payload schemas:

```yaml
# cosalette-requirements.yaml
capabilities:
  battery_powered:
    required_channels:
      - pattern: "{app}/{device}/battery"
        payload_ref: "asyncapi.yaml#/components/schemas/BatteryPayload"
```

This separates concerns cleanly (AsyncAPI for structure, manifest for rules) at the
cost of maintaining two files in sync.

**Option 3: `oneOf` / discriminator patterns.**
JSON Schema's `oneOf` with `if/then/else` could model conditional requirements within
the payload schema, but not across channels. A device's presence on one topic cannot
be conditioned on properties of another topic within AsyncAPI alone.

**Conclusion:** AsyncAPI handles topic/payload structure well but needs augmentation
for capability-based enforcement. Option 1 (extension properties) is the pragmatic
choice for a first implementation — it keeps the schema in one file and the custom
validator is straightforward. Option 2 becomes attractive if the requirements layer
grows complex enough to warrant separation.

#### Python Tooling Ecosystem

**`asyncapi` Python package.** Provides basic parsing and validation. The package is
not heavily maintained and lags behind the 3.0.0 specification. Usable for loading
documents but not for production-grade validation.

**`jsonschema` library.** Since AsyncAPI message payloads _are_ JSON Schema, the real
validation workhorse is the mature `jsonschema` package (or `fastjsonschema` for
speed). The framework would extract payload schemas from the AsyncAPI document and
validate using `jsonschema.validate()`:

```python
import yaml
from jsonschema import validate, ValidationError

with open("asyncapi.yaml") as f:
    spec = yaml.safe_load(f)

schema = spec["components"]["schemas"]["TemperaturePayload"]
validate(instance={"temperature": 22.5, "unit": "celsius"}, schema=schema)
```

**Manual loading approach.** Parse the AsyncAPI YAML with `pyyaml` or `ruamel.yaml`,
resolve `$ref` pointers, extract channel→message→schema mappings, and feed payloads
to `jsonschema`. This is straightforward and avoids dependency on the immature
`asyncapi` package.

**Code generation.** Python generators for AsyncAPI are limited compared to the
Java/TypeScript ecosystems. The official generator (`@asyncapi/generator`) supports
Python templates but they target simple HTTP clients, not MQTT frameworks. cosalette
would likely need custom generation to produce typed `TypedDict` or `dataclass`
definitions from schemas.

**Assessment.** Python tooling for AsyncAPI is _usable but immature_. The core
validation path (YAML parse → schema extract → `jsonschema` validate) is robust and
production-ready. Advanced features (code generation, documentation rendering) would
require custom implementations.

#### AsyncAPI 3.x vs 2.x

| Feature                         | 2.x                                       | 3.0.0                                              |
| ------------------------------- | ------------------------------------------ | --------------------------------------------------- |
| Channel identity                | Keyed by address string                    | Named object with `address` field                   |
| Channel/operation separation    | Combined in channel                        | Separate objects — cleaner for cosalette's model     |
| Parameters                      | Part of channel key                        | First-class `parameters` object with `enum`/`default`|
| Reply mechanism                 | None                                       | Built-in — useful if cosalette adds request/reply    |
| Server variables                | Supported                                  | Enhanced with `enum` constraints                     |
| Reuse model                     | `$ref` + components                        | `$ref` + components + traits (more composable)       |

The 3.0.0 channel/operation separation maps cleanly to cosalette's archetype model:
a `@app.device` becomes two operations (send + receive) on the same channel parameters,
rather than a single channel with mixed pub/sub semantics. The named-channel model also
makes it easier to reference specific channels from extension properties
(`x-cosalette-requires`).

**Recommendation:** Target AsyncAPI 3.0.0 exclusively. The 2.x model conflates channels
and operations in ways that make cosalette's archetype mapping awkward.

#### Verdict

AsyncAPI 3.0.0 is a **strong fit** for cosalette's topic/payload structure layer:

- Channel Address Expressions map directly to ADR-002's `{app}/{device}/{suffix}`
  convention.
- The send/receive operation model maps to cosalette's telemetry/command/device
  archetypes.
- MQTT bindings express retain and QoS at the operation level — exactly where cosalette
  needs them.
- JSON Schema payloads provide type-safe enforcement using the mature `jsonschema`
  ecosystem.
- Message and Operation Traits reduce repetition across devices.

The gap is **capability-based conditional enforcement** — AsyncAPI alone cannot express
"if device has tag X, require channel Y." This requires augmentation via extension
properties or a companion manifest.

---

### 2.2 Schema Format Alternatives (COS-5hx.2)

#### Option A: Plain JSON Schema + Custom YAML Manifest

A YAML manifest defines the topic tree. JSON Schema files define payload shapes per
topic pattern. No AsyncAPI specification overhead.

**Manifest structure:**

```yaml
# schema/vito2mqtt.manifest.yaml
app: vito2mqtt
version: "0.2.0"

topics:
  mandatory:
    - pattern: "{app}/status"
      payload: schemas/status.json
      retain: true
      qos: 1
    - pattern: "{app}/error"
      payload: schemas/error.json
      retain: false
      qos: 0
    - pattern: "{app}/{device}/availability"
      payload: schemas/availability.json
      retain: true
      qos: 1

  per_device:
    - pattern: "{app}/{device}/state"
      payload_ref: "schemas/devices/{device}.json"
      retain: true
      qos: 1
    - pattern: "{app}/{device}/set"
      payload_ref: "schemas/commands/{device}.json"
      retain: false
      qos: 1

devices:
  temperature:
    type: telemetry
    tags: [temperature_sensor]
    state_schema:
      type: object
      required: [temperature, unit]
      properties:
        temperature: { type: number }
        unit: { type: string, enum: [celsius, fahrenheit] }

  valve:
    type: bidirectional
    tags: [actuator]
    state_schema:
      type: object
      required: [position]
      properties:
        position: { type: integer, minimum: 0, maximum: 100 }
    command_schema:
      type: object
      required: [position]
      properties:
        position: { type: integer, minimum: 0, maximum: 100 }

capabilities:
  battery_powered:
    requires:
      - channel: "{app}/{device}/battery"
        payload:
          type: object
          required: [level, charging]
          properties:
            level: { type: integer, minimum: 0, maximum: 100 }
            charging: { type: boolean }
```

**Pros:**

- Simpler than AsyncAPI — no specification to learn beyond JSON Schema.
- Full JSON Schema ecosystem for payload validation (`jsonschema`, `fastjsonschema`).
- Tiny runtime footprint: `pyyaml` + `jsonschema` (both already in cosalette's
  dependency tree or trivially added).
- Capability-based requirements are native citizens, not extension properties.
- Custom format can evolve freely with cosalette without waiting for specification
  updates.

**Cons:**

- No standard tooling for documentation generation — cannot use AsyncAPI Studio,
  Redoc, or other renderers.
- Custom parser and validator needed — this is code to write and maintain.
- No community ecosystem — only useful within cosalette.
- No interoperability with external MQTT tools that understand AsyncAPI.

#### Option B: Home Assistant MQTT Discovery as Implicit Schema

Home Assistant's MQTT Discovery protocol publishes device capabilities via discovery
topics (`homeassistant/{component}/{node_id}/{object_id}/config`). cosalette already
partially supports HA discovery. Discovery payloads could serve as an implicit schema
source.

**How it works today:**

```json
{
  "name": "Temperature",
  "state_topic": "vito2mqtt/temperature/state",
  "unit_of_measurement": "°C",
  "value_template": "{{ value_json.temperature }}",
  "device": {
    "identifiers": ["vito2mqtt_temperature"],
    "name": "Vitoconnect Temperature"
  }
}
```

The discovery payload declares which topic to read, what template to extract data from,
and the unit. In principle, this _is_ a schema — it defines the expected structure of
the state topic payload.

**Pros:**

- Free integration with Home Assistant — HA will auto-discover entities.
- Widely adopted in the smart home community.
- Self-documenting for HA users.
- cosalette already has partial HA discovery support.

**Cons:**

- **HA-specific.** Non-HA consumers (Grafana, custom dashboards, other MQTT clients)
  gain nothing from discovery payloads.
- **Does not cover payload validation.** Discovery declares _how to read_ a payload
  (`value_template`), not _what shape the payload must have_. There's no JSON Schema
  equivalent.
- **Discovery ≠ enforcement.** HA discovery is an output (published at runtime), not
  an input (validated at development time). The framework cannot use it to reject
  invalid registrations before publishing.
- **Cannot express inter-app or inter-device requirements.** There is no mechanism to
  say "device X requires channel Y."

**Assessment:** HA discovery is valuable as an **output target** — cosalette should
continue to support it. But it is not suitable as the **source schema format** for
enforcement. It lacks JSON Schema payloads, has no validation semantics, and is
tied to one consumer ecosystem.

#### Option C: Custom Lightweight Manifest

A cosalette-specific YAML/JSON format designed precisely for the framework's needs.
Unlike Option A (which separates manifest from schemas), this format is a single
self-contained document combining topic patterns, payload schemas, capability
requirements, and enforcement rules.

**Example:**

```yaml
# cosalette-schema.yaml
cosalette_schema: "1.0"
app: vito2mqtt
version: "0.2.0"

defaults:
  state:
    retain: true
    qos: 1
  command:
    retain: false
    qos: 1
  availability:
    retain: true
    qos: 1

mandatory_topics:
  app_status:
    topic: "{app}/status"
    direction: publish
    retain: true
    qos: 1
    payload:
      type: object
      required: [online]
      properties:
        online: { type: boolean }

  app_error:
    topic: "{app}/error"
    direction: publish
    retain: false
    qos: 0
    payload:
      type: object
      required: [error, timestamp]
      properties:
        error: { type: string }
        timestamp: { type: string, format: date-time }

  device_availability:
    topic: "{app}/{device}/availability"
    direction: publish
    payload:
      type: object
      required: [online]
      properties:
        online: { type: boolean }

devices:
  temperature:
    archetype: telemetry
    tags: [temperature_sensor]
    state:
      payload:
        type: object
        required: [temperature, unit]
        properties:
          temperature: { type: number }
          unit: { type: string, enum: [celsius, fahrenheit] }
        additionalProperties: false

  valve:
    archetype: device  # bidirectional
    tags: [actuator]
    state:
      payload:
        type: object
        required: [position]
        properties:
          position: { type: integer, minimum: 0, maximum: 100 }
        additionalProperties: false
    command:
      payload:
        type: object
        required: [position]
        properties:
          position: { type: integer, minimum: 0, maximum: 100 }

capabilities:
  battery_powered:
    description: Devices with battery must report battery status
    required_topics:
      - suffix: battery
        direction: publish
        payload:
          type: object
          required: [level, charging]
          properties:
            level: { type: integer, minimum: 0, maximum: 100 }
            charging: { type: boolean }

enforcement:
  mode: strict  # strict | warn | off
  hooks:
    on_configure: true   # validate registrations during on_configure
    on_publish: true     # validate payloads before MQTT publish
```

**Pros:**

- **Perfect fit for cosalette.** Every concept in the format maps to a framework
  concept — archetypes, tags, capabilities, enforcement modes.
- **Simplest possible runtime.** Parse YAML, iterate devices, validate with
  `jsonschema`. No specification to interpret.
- **Full control over evolution.** Adding new features (e.g., coalescing group
  validation, cron schedule constraints) is a YAML key addition, not a specification
  extension negotiation.
- **Single file.** No `$ref` chains, no multi-file resolution.
- **Capability enforcement is a first-class citizen,** not bolted on via extensions.

**Cons:**

- **No community tooling.** No documentation generators, no editor plugins, no
  standard validators beyond what cosalette builds.
- **Only useful within cosalette.** External tools that want to understand the MQTT
  contract must learn a custom format.
- **Risk of reinventing AsyncAPI badly.** As requirements grow, the custom format may
  converge towards a poorly-specified subset of AsyncAPI.
- **Documentation generation is DIY.** Generating human-readable topic/payload docs
  from the manifest requires a custom renderer.

---

### 2.3 Comparison Matrix and Recommendation (COS-5hx.3)

#### Weighted Scoring Matrix

Nine evaluation dimensions, weighted by importance to cosalette's runtime and
development workflow. Each option scores 1–5 (1 = poor, 5 = excellent).

| #  | Dimension                  | Weight | AsyncAPI 3.0.0 | Option A (JSON Schema + Manifest) | Option B (HA Discovery) | Option C (Custom Manifest) |
|----|----------------------------|--------|----------------|-----------------------------------|-------------------------|----------------------------|
| 1  | Expressiveness             | 3      | 4              | 4                                 | 1                       | 5                          |
| 2  | Payload validation         | 3      | 5              | 5                                 | 1                       | 5                          |
| 3  | Capability enforcement     | 3      | 2              | 4                                 | 1                       | 5                          |
| 4  | Tooling ecosystem          | 2      | 4              | 3                                 | 3                       | 1                          |
| 5  | Runtime footprint          | 2      | 3              | 4                                 | 5                       | 5                          |
| 6  | Learning curve             | 1      | 2              | 3                                 | 4                       | 5                          |
| 7  | Interoperability           | 1      | 5              | 2                                 | 3                       | 1                          |
| 8  | Evolvability               | 2      | 3              | 4                                 | 1                       | 5                          |
| 9  | Distribution flexibility   | 2      | 4              | 4                                 | 2                       | 4                          |

**Scoring rationale by dimension:**

1. **Expressiveness (×3).** AsyncAPI (4): channel/operation model maps to archetypes
   (ADR-010) and sub-topic routing (ADR-025), parametrised addresses handle
   `{app}/{device}/{suffix}`; loses a point because conditional capability requirements
   need `x-cosalette-*` extensions. Option A (4): same modelling power via custom YAML
   keys; loses a point for multi-file resolution. Option B (1): cannot express topic
   trees, payload shapes, or capability requirements — discovery payloads are
   consumer instructions, not structural schemas. Option C (5): every cosalette concept
   (archetypes, tags, capabilities, coalescing groups, enforcement modes) is a native
   citizen.

2. **Payload validation (×3).** AsyncAPI (5): message payloads _are_ JSON Schema
   Draft 07+; the `jsonschema` library validates directly. Option A (5): identical —
   payloads are standalone JSON Schema files. Option B (1): no JSON Schema; only
   Jinja2 `value_template` strings with no validation semantics. Option C (5): inline
   JSON Schema under each device's `state`/`command` key.

3. **Capability enforcement (×3).** AsyncAPI (2): cannot natively express "if tag X,
   require channel Y" — needs `x-cosalette-requires` extension properties and a custom
   validator. Option A (4): the manifest's `capabilities` block is purpose-built for
   tag-based requirements; loses a point because payloads live in separate files.
   Option B (1): no mechanism for inter-device or tag-based conditions. Option C (5):
   first-class `capabilities` and `enforcement` blocks designed for this exact use case.

4. **Tooling ecosystem (×2).** AsyncAPI (4): AsyncAPI Studio, Redoc-style
   documentation, Spectral linting, JSON Schema editor support; loses a point because
   Python-specific tooling lags behind TypeScript/Java. Option A (3): JSON Schema
   editors and validators are mature, but the manifest format has no external tooling.
   Option B (3): HA itself is rich tooling, but it is consumer-side only — no
   development-time validation. Option C (1): all tooling is DIY.

5. **Runtime footprint (×2).** AsyncAPI (3): requires YAML parsing, `$ref` resolution
   across a large document, and schema extraction before `jsonschema` validation —
   acceptable on x86, noticeable on Raspberry Pi. Option A (4): simpler YAML +
   standalone JSON Schema files; `$ref` resolution is scoped. Option B (5): no schema
   parsing at all (discovery is an _output_). Option C (5): single YAML file, flat
   structure, direct `jsonschema` calls — minimal memory and import time.

6. **Learning curve (×1).** AsyncAPI (2): the 3.0.0 specification is substantial;
   developers must understand channels, operations, bindings, traits, and $ref
   resolution. Option A (3): JSON Schema is widely known; the custom manifest adds a
   thin layer. Option B (4): HA developers know discovery well. Option C (5):
   cosalette-native concepts with no external specification to learn.

7. **Interoperability (×1).** AsyncAPI (5): industry standard understood by external
   MQTT tools, documentation platforms, and API governance systems. Option A (2):
   JSON Schema payloads are portable, but the manifest format is proprietary.
   Option B (3): interoperable with Home Assistant only. Option C (1): opaque to all
   external tooling.

8. **Evolvability (×2).** AsyncAPI (3): adding cosalette features requires new
   extension properties, which accumulate outside the specification's governance;
   specification updates may break extensions. Option A (4): custom manifest evolves
   freely but must also track JSON Schema file locations. Option C (5): full control
   over format evolution — adding coalescing group validation (ADR-018) or cron
   constraints is a YAML key addition.

9. **Distribution flexibility (×2).** AsyncAPI (4): single YAML file is easy to serve
   via HTTP, embed in MQTT retained messages, or ship as a local file; large documents
   may strain retained-message size limits. Option A (4): manifest + schema files need
   bundling or a resolution strategy, but individual pieces are small. Option B (2):
   distribution is MQTT-only (discovery topics); no HTTP or file-system story.
   Option C (4): single compact YAML, trivially distributed by any transport.

**Weighted totals:**

| Option                          | Calculation                                                                                           | Total |
|---------------------------------|-------------------------------------------------------------------------------------------------------|-------|
| **AsyncAPI 3.0.0**              | (4×3) + (5×3) + (2×3) + (4×2) + (3×2) + (2×1) + (5×1) + (3×2) + (4×2) = 12+15+6+8+6+2+5+6+8       | **68** |
| **Option A (JSON Schema + Manifest)** | (4×3) + (5×3) + (4×3) + (3×2) + (4×2) + (3×1) + (2×1) + (4×2) + (4×2) = 12+15+12+6+8+3+2+8+8 | **74** |
| **Option B (HA Discovery)**     | (1×3) + (1×3) + (1×3) + (3×2) + (5×2) + (4×1) + (3×1) + (1×2) + (2×2) = 3+3+3+6+10+4+3+2+4        | **38** |
| **Option C (Custom Manifest)**  | (5×3) + (5×3) + (5×3) + (1×2) + (5×2) + (5×1) + (1×1) + (5×2) + (4×2) = 15+15+15+2+10+5+1+10+8    | **81** |

#### Qualitative Analysis

**Viable options vs. non-starters.**

Option B (HA Discovery) scores 38 — well below the viable threshold. It is an **output
target**, not a source schema. Discovery payloads tell Home Assistant _how to consume_
topics; they carry no JSON Schema, no validation semantics, and no mechanism for
cross-device requirements. cosalette should continue publishing HA discovery messages
at runtime, but they play no role in schema enforcement. **Option B is eliminated from
further consideration.**

The remaining three options are all technically viable. The question is where on the
spectrum between "maximise cosalette fit" and "maximise industry alignment" the project
should land.

**Option C scores highest — but carries strategic risk.**

Option C (Custom Manifest) wins on raw points because every dimension that matters most
to cosalette (expressiveness, capability enforcement, evolvability) is a perfect fit. It
is also the simplest runtime path: parse one YAML, call `jsonschema`, done.

The risk is **reinventing AsyncAPI badly.** As schema enforcement matures, the custom
format will accumulate features that AsyncAPI already specifies: `$ref` for schema
reuse, binding declarations for QoS/retain, trait composition for shared properties,
server definitions for multi-broker deployments. Each addition increases the
specification surface that cosalette alone must maintain, document, and validate. A year
from now, the custom format may be a poorly-specified, poorly-documented superset of the
problems AsyncAPI solves — without the community, tooling, or editorial oversight that
keeps AsyncAPI coherent.

This risk is not hypothetical. The Section 2.2 example already uses inline JSON Schema,
`$ref`-like payload references, default blocks, and enforcement modes — structural
concepts that AsyncAPI has formal definitions for. Each new dimension (coalescing groups
from ADR-018, retry backoff from ADR-024, command sub-topic routing from ADR-025)
pushes the custom format closer to AsyncAPI's territory.

**Option A is the middle ground nobody loves.**

Option A (Plain JSON Schema + Custom Manifest) avoids AsyncAPI's specification weight
while retaining JSON Schema's ecosystem. But it shares Option C's strategic risk
(custom manifest that grows unbounded) while splitting schema definitions across
multiple files — adding file resolution complexity without gaining community tooling.
It scores well but occupies an awkward position: too custom to be interoperable, too
fragmented to be simple.

**AsyncAPI's "specification overhead" concern is manageable.**

The learning-curve penalty for AsyncAPI (score: 2) reflects the 3.0.0 specification's
size. In practice, cosalette needs a _subset_: channels, operations, messages, schemas,
MQTT bindings, and traits. Developers never touch servers, security schemes, or protocol
bindings beyond MQTT. A well-commented template AsyncAPI document — generated by the
framework at `cosalette schema init` — reduces the learning curve to "fill in your
device schemas." The specification overhead is a one-time cost amortised across every
app built with cosalette.

#### Hybrid Approach: AsyncAPI + `x-cosalette-*` Extensions

The qualitative analysis exposes a tension: AsyncAPI provides the right _structure_
(channels, operations, payloads, MQTT bindings) but lacks cosalette-specific _semantics_
(capability enforcement, archetype mapping, enforcement modes). Option C provides the
right semantics but lacks industry alignment. The hybrid approach resolves this by
layering cosalette semantics onto AsyncAPI's structural foundation.

**How it works:**

1. **AsyncAPI 3.0.0 is the canonical document format.** All topic definitions, payload
   schemas, MQTT bindings, and message traits live in standard AsyncAPI YAML. Any
   AsyncAPI-aware tool can parse, render, and lint the document.

2. **`x-cosalette-*` extension properties carry cosalette semantics.** The AsyncAPI
   specification permits vendor extensions (`x-` prefixed keys) on most objects.
   cosalette uses these to express:

   - **`x-cosalette-archetype`** on operations — links an AsyncAPI operation to a
     cosalette archetype (`telemetry`, `command`, `device`), matching ADR-010.
   - **`x-cosalette-requires`** on channels — declares tag-based capability
     requirements ("if device has tag `battery_powered`, this channel is required").
   - **`x-cosalette-enforcement`** at the document root — sets the enforcement mode
     (`strict`, `warn`, `off`) and hooks (`on_configure`, `on_publish`).
   - **`x-cosalette-coalescing-group`** on operations — references coalescing group
     membership per ADR-018.

   Example combining standard AsyncAPI with cosalette extensions:

   ```yaml
   asyncapi: 3.0.0
   info:
     title: vito2mqtt
     version: 0.2.0
   x-cosalette-enforcement:
     mode: strict
     hooks:
       on_configure: true
       on_publish: true

   channels:
     batteryState:
       address: "{appName}/{deviceName}/battery"
       x-cosalette-requires:
         tag: battery_powered
       messages:
         batteryReading:
           payload:
             type: object
             required: [level, charging]
             properties:
               level: { type: integer, minimum: 0, maximum: 100 }
               charging: { type: boolean }

   operations:
     publishTemperature:
       action: send
       channel:
         $ref: "#/channels/temperatureState"
       x-cosalette-archetype: telemetry
       x-cosalette-coalescing-group: climate_sensors
       bindings:
         mqtt:
           qos: 1
           retain: true
   ```

3. **Custom tooling extracts and validates extensions.** The framework loads the
   AsyncAPI YAML, resolves `$ref` pointers, and processes `x-cosalette-*` properties.
   Standard payload validation uses `jsonschema`; capability enforcement iterates
   `x-cosalette-requires` channels against the device registry. The `asyncapi` Python
   package is not required — `pyyaml` + `jsonschema` suffice.

**When would the hybrid tip toward a full custom format?**

The hybrid approach holds as long as `x-cosalette-*` extensions remain _metadata on
existing AsyncAPI objects_. The tipping point is when cosalette needs structural
concepts that AsyncAPI does not model — objects not expressible as properties on
channels, operations, or messages. Specific triggers:

- **Cross-document references.** If enforcement requires correlating schemas across
  multiple apps (e.g., "app A's output schema must match app B's input schema"),
  AsyncAPI's single-document model becomes constraining.
- **Dynamic schema generation.** If the schema must be computed at runtime from the
  device registry (rather than authored statically), AsyncAPI's static YAML format
  adds friction.
- **Extension proliferation.** If `x-cosalette-*` keys outnumber standard AsyncAPI
  keys in a typical document, the AsyncAPI wrapper provides diminishing value — the
  document is effectively a custom format wearing an AsyncAPI coat.

None of these triggers are imminent. cosalette's current needs (topic structure, payload
validation, capability enforcement, MQTT bindings) sit comfortably within AsyncAPI's
object model plus lightweight extensions.

#### Recommendation

**Primary format: AsyncAPI 3.0.0 with `x-cosalette-*` extensions (the hybrid
approach).**

**Rationale:**

1. **Industry-standard structure.** Channel addresses, operations, MQTT bindings,
   message traits, and JSON Schema payloads are defined by a governed specification
   with community tooling. cosalette does not maintain these definitions.

2. **Lightweight extension for cosalette semantics.** Capability enforcement
   (`x-cosalette-requires`), archetype mapping (`x-cosalette-archetype`), enforcement
   modes (`x-cosalette-enforcement`), and coalescing groups
   (`x-cosalette-coalescing-group`) are thin metadata layers — not a parallel
   specification.

3. **Proven validation path.** The runtime validation pipeline is `pyyaml` →
   `$ref` resolution → `jsonschema` for payloads → custom logic for `x-cosalette-*`
   enforcement. No immature AsyncAPI-specific Python library required.

4. **Documentation for free.** AsyncAPI Studio, Redoc-style renderers, and Spectral
   linting work out of the box for the standard portion of the document. Custom
   renderers extend coverage to `x-cosalette-*` properties as needed.

5. **Strategic alignment.** If cosalette schemas are ever published externally (e.g.,
   for third-party integration), the AsyncAPI container is immediately legible to the
   broader MQTT/event-driven community — the `x-cosalette-*` properties degrade
   gracefully (ignored by standard tooling, meaningful to cosalette-aware consumers).

**Fallback path: Option C (Custom Lightweight Manifest).**

If AsyncAPI proves too heavyweight in practice — specifically if the specification's
YAML structure creates friction for simple cosalette apps with 2–3 devices — the
project pivots to Option C. The migration cost is bounded: payload schemas (JSON Schema)
are portable between formats, and `x-cosalette-*` properties map directly to
Option C's native keys.

**Pivot triggers (any one is sufficient):**

- `x-cosalette-*` extension properties exceed 40% of the document's total key count
  across three or more production apps.
- AsyncAPI specification updates (4.x) break backward compatibility for `x-` extensions
  in ways that require non-trivial migration.
- Developer feedback consistently cites AsyncAPI overhead as a barrier to schema
  adoption in apps with fewer than 5 devices.

**Immediate next steps for the recommended approach:**

1. **Define the `x-cosalette-*` extension schema.** Author a JSON Schema for each
   extension property (`x-cosalette-requires`, `x-cosalette-archetype`,
   `x-cosalette-enforcement`, `x-cosalette-coalescing-group`) to enable validation
   of the extensions themselves.

2. **Build the schema loader.** Implement a Python module that parses an AsyncAPI 3.0.0
   YAML document, resolves `$ref` pointers, extracts channel→message→schema mappings,
   and collects `x-cosalette-*` metadata into a `SchemaRegistry` object.

3. **Implement `on_configure` capability enforcement.** Wire the schema loader into the
   `on_configure` lifecycle phase (ADR-023) to validate device registrations against
   `x-cosalette-requires` channels.

4. **Author the vito2mqtt reference schema.** Create a complete AsyncAPI 3.0.0 document
   for the vito2mqtt example app, exercising all extension properties, as a template
   for other apps.

5. **Add `cosalette schema init` CLI command.** Generate a starter AsyncAPI document
   from the device registry at `build_registry_snapshot()` time, pre-populated with
   channels, operations, and placeholder payload schemas.

---

## 3. Use Cases

### UC1: Device Capability Enforcement (COS-5hx.4)

#### Problem

A cosalette app registers a device with the tag `battery_powered`, but the developer
forgets to include a `/battery` topic handler. The omission is invisible until a
downstream consumer (Home Assistant dashboard, monitoring system) fails to find battery
data. There is no framework mechanism to enforce that tagged devices meet their
contractual obligations.

#### Schema Definition

Using AsyncAPI with extension properties:

```yaml
channels:
  batteryState:
    address: "{appName}/{deviceName}/battery"
    x-cosalette-requires:
      tag: battery_powered
    messages:
      batteryReading:
        payload:
          type: object
          required: [level, charging]
          properties:
            level:
              type: integer
              minimum: 0
              maximum: 100
            charging:
              type: boolean
```

Using the custom manifest (Option C):

```yaml
capabilities:
  battery_powered:
    description: Devices with battery must report battery status
    required_topics:
      - suffix: battery
        direction: publish
        payload:
          type: object
          required: [level, charging]
          properties:
            level: { type: integer, minimum: 0, maximum: 100 }
            charging: { type: boolean }
```

#### Framework Enforcement Sketch

The natural enforcement point is the `on_configure` lifecycle phase (ADR-023). After
all hooks have registered devices and before adapter `__aenter__`, the framework can
compare the registry snapshot against schema requirements:

```python
from cosalette._introspect import build_registry_snapshot
from cosalette._schema import load_schema, validate_capabilities


@app.on_configure
def enforce_schema(settings: AppSettings) -> None:
    schema = load_schema(settings.schema_path)
    snapshot = build_registry_snapshot(app)
    violations = validate_capabilities(schema, snapshot)
    if violations:
        raise SchemaViolation(violations)
```

The `validate_capabilities` function walks the schema's capability definitions, finds
all devices with matching tags in the snapshot, and checks that each required topic
suffix has a corresponding registration:

```python
def validate_capabilities(
    schema: CoseletteSchema,
    snapshot: dict[str, Any],
) -> list[str]:
    """Check that tagged devices satisfy capability requirements."""
    violations: list[str] = []
    all_devices = _collect_devices(snapshot)

    for cap_name, cap_def in schema.capabilities.items():
        for device in all_devices:
            if cap_name in device.tags:
                for req in cap_def.required_topics:
                    topic = f"{schema.app}/{device.name}/{req.suffix}"
                    if not _has_registration(snapshot, device.name, req.suffix):
                        violations.append(
                            f"device '{device.name}' tagged '{cap_name}' "
                            f"but missing required channel '{topic}'"
                        )
    return violations
```

#### Expected Developer Experience

```text
$ uv run python -m vito2mqtt
cosalette.SchemaViolation: 1 schema violation(s):

  • device 'motion_sensor' tagged 'battery_powered' but missing required
    channel 'vito2mqtt/motion_sensor/battery'

    Capability 'battery_powered' requires a publish registration with
    suffix 'battery' and payload schema:
      {"type": "object", "required": ["level", "charging"], ...}

    Fix: add a telemetry handler for 'motion_sensor/battery' or remove
    the 'battery_powered' tag from the device.
```

The error is fatal during startup — the app cannot run with unsatisfied capability
contracts. This matches cosalette's existing convention where `on_configure` exceptions
are fatal (ADR-023).

---

### UC2: Mandatory Topics (COS-5hx.4)

#### Problem

ADR-002 specifies that every app must publish `{app}/status` and every device must
have an `{app}/{device}/availability` topic. cosalette enforces this by convention —
the framework wires these topics automatically. But there is no machine-verifiable
assertion that the convention is upheld, and no way to declare _additional_ mandatory
topics beyond the framework defaults.

For example, a deployment policy might require every app to publish
`{app}/diagnostics` with `{uptime_seconds: int, version: string}`. Today this can
only be communicated via documentation.

#### Schema Definition

```yaml
mandatory_topics:
  app_status:
    topic: "{app}/status"
    direction: publish
    retain: true
    qos: 1
    payload:
      type: object
      required: [online]
      properties:
        online: { type: boolean }

  device_availability:
    topic: "{app}/{device}/availability"
    direction: publish
    retain: true
    qos: 1
    payload:
      type: object
      required: [online]
      properties:
        online: { type: boolean }

  # Custom mandatory topic beyond framework defaults
  app_diagnostics:
    topic: "{app}/diagnostics"
    direction: publish
    retain: true
    qos: 1
    payload:
      type: object
      required: [uptime_seconds, version]
      properties:
        uptime_seconds: { type: integer, minimum: 0 }
        version: { type: string }
```

#### Framework Enforcement Sketch

During registration, the framework checks that all mandatory topics are covered by
registered devices, telemetry handlers, or framework-wired topics:

```python
def validate_mandatory_topics(
    schema: CoseletteSchema,
    snapshot: dict[str, Any],
) -> list[str]:
    """Ensure all mandatory topics have corresponding registrations."""
    violations: list[str] = []
    app_name = snapshot["app"]["name"]
    device_names = [d["name"] for d in snapshot["devices"]]
    device_names += [t["name"] for t in snapshot["telemetry"]]
    device_names += [c["name"] for c in snapshot["commands"]]
    device_names = list(set(device_names))

    for topic_id, topic_def in schema.mandatory_topics.items():
        pattern = topic_def.topic

        if "{device}" in pattern:
            # Per-device mandatory topic — check every device
            for device in device_names:
                concrete = pattern.format(app=app_name, device=device)
                if not _topic_is_covered(snapshot, concrete, topic_def.direction):
                    violations.append(
                        f"mandatory topic '{concrete}' ({topic_id}) "
                        f"has no registration"
                    )
        else:
            # App-level mandatory topic
            concrete = pattern.format(app=app_name)
            if not _topic_is_covered(snapshot, concrete, topic_def.direction):
                violations.append(
                    f"mandatory app topic '{concrete}' ({topic_id}) "
                    f"has no registration"
                )

    return violations
```

For framework-wired topics (`{app}/status`, `{app}/{device}/availability`), the
validator recognizes them as implicitly covered — the framework guarantees their
presence. Custom mandatory topics (e.g. `{app}/diagnostics`) must have an explicit
registration.

#### Expected Developer Experience

```text
$ uv run python -m vito2mqtt
cosalette.SchemaViolation: 1 schema violation(s):

  • mandatory app topic 'vito2mqtt/diagnostics' (app_diagnostics)
    has no registration

    Required payload schema:
      {"type": "object", "required": ["uptime_seconds", "version"], ...}

    Fix: add a telemetry handler publishing to 'vito2mqtt/diagnostics'
    with the required payload shape.
```

---

### UC3: Payload Shape Validation (COS-5hx.4)

#### Problem

A telemetry handler returns `{"temp": 22.5}` instead of the expected
`{"temperature": 22.5, "unit": "celsius"}`. The payload is published to MQTT without
any validation. Downstream consumers that expect the documented shape receive malformed
data. The developer discovers the mistake only when a dashboard panel shows "N/A" or a
consumer throws an exception.

#### Schema Definition

The payload schema for the `temperature` device's state topic:

```yaml
devices:
  temperature:
    archetype: telemetry
    state:
      payload:
        type: object
        required: [temperature, unit]
        properties:
          temperature: { type: number }
          unit: { type: string, enum: [celsius, fahrenheit] }
        additionalProperties: false
```

#### Framework Enforcement Sketch

**Mode selection.** The validation mode is set at `App` construction:

```python
app = App(
    name="vito2mqtt",
    schema="schema/vito2mqtt.yaml",         # path to schema file
    schema_validation="strict",             # "strict" | "warn" | "off"
)
```

- `strict` — raises `PayloadValidationError` before publishing. Used during
  development and CI.
- `warn` — logs a warning with the validation error but publishes the payload. Used
  in production when schemas are new and might have false positives.
- `off` — no validation. Zero overhead. Default when no schema is provided.

**Publish hook.** The validation is inserted in the publish path, after the telemetry
handler returns and before the MQTT client publishes:

```python
import jsonschema


class _PayloadValidator:
    """Validates outgoing payloads against the loaded schema."""

    def __init__(self, schema: CoseletteSchema, mode: str) -> None:
        self._schema = schema
        self._mode = mode
        # Pre-compile validators for each device/topic combination
        self._validators: dict[str, jsonschema.Draft7Validator] = {}
        self._build_validators()

    def _build_validators(self) -> None:
        for device_name, device_def in self._schema.devices.items():
            if device_def.state and device_def.state.payload:
                topic = f"{self._schema.app}/{device_name}/state"
                self._validators[topic] = jsonschema.Draft7Validator(
                    device_def.state.payload,
                )

    def validate(self, topic: str, payload: dict[str, object]) -> None:
        """Validate a payload about to be published.

        Raises PayloadValidationError in strict mode, logs in warn mode.
        """
        validator = self._validators.get(topic)
        if validator is None:
            return  # no schema for this topic — skip

        errors = list(validator.iter_errors(payload))
        if not errors:
            return

        messages = [e.message for e in errors]
        if self._mode == "strict":
            raise PayloadValidationError(topic, messages)
        elif self._mode == "warn":
            logger.warning(
                "Payload validation warnings for '%s': %s",
                topic,
                "; ".join(messages),
            )
```

**Integration point.** The validator is called in the telemetry publish path:

```python
async def _publish_state(
    self,
    device_name: str,
    payload: dict[str, object],
) -> None:
    topic = f"{self._app_name}/{device_name}/state"

    # Schema validation before publish
    if self._payload_validator is not None:
        self._payload_validator.validate(topic, payload)

    await self._mqtt.publish(topic, orjson.dumps(payload), retain=True, qos=1)
```

#### Expected Developer Experience

**Strict mode (development/CI):**

```text
$ uv run python -m vito2mqtt
cosalette.PayloadValidationError: payload for 'vito2mqtt/temperature/state'
failed schema validation:

  • 'temperature' is a required property
  • Additional properties are not allowed ('temp' was unexpected)

  Payload:   {"temp": 22.5}
  Expected:  {"temperature": <number>, "unit": "celsius"|"fahrenheit"}
  Schema:    schema/vito2mqtt.yaml → devices.temperature.state.payload
```

**Warn mode (production):**

```text
2026-04-08 14:23:01 WARNING cosalette.schema: Payload validation warnings
for 'vito2mqtt/temperature/state': 'temperature' is a required property;
Additional properties are not allowed ('temp' was unexpected)
```

The payload is published regardless in warn mode — the warning surfaces the issue
without disrupting the running app.

---

### 3.4 UC4: Schema Distribution and Update (COS-5hx.5)

#### Problem

cosalette has no mechanism to distribute a schema document to running applications.
Today, each app either hard-codes a local schema path or has no schema at all.
Developers manually ensure compliance by reading documentation. There is no central
update workflow — changing a payload shape means editing YAML on every machine that
runs the app, then restarting. In a fleet of Raspberry Pis running heterogeneous
cosalette apps, this manual process is error-prone and unscalable.

Concrete scenario: an operator adds a mandatory `diagnostics` topic to the network
schema. Five cosalette apps run across three hosts. The operator must SSH into each
host, update the schema file, and restart every app. If one host is unreachable
(rebooting, network partition), that app continues running against the stale schema.

#### Schema Definition

The schema itself is an AsyncAPI 3.0.0 document with `x-cosalette-*` extensions,
as recommended in Section 2.3. The _distribution_ mechanism is orthogonal to the
schema format — the same YAML document can be served via MQTT, HTTP, or a local file.

**Schema source configuration via cosalette settings:**

```yaml
# AsyncAPI document declaring its own distribution preferences
asyncapi: 3.0.0
info:
  title: vito2mqtt
  version: 1.0.0
x-cosalette-enforcement:
  mode: warn
  hooks:
    on_configure: true
    on_publish: true
  schema_source: "mqtt://cosalette/schema/vito2mqtt"
```

**Three distribution approaches compared:**

| Approach | Address | Pros | Cons |
|----------|---------|------|------|
| MQTT retained message | `cosalette/schema/{app}` | Zero infrastructure, works offline, arrives via existing broker | Size-limited (~256 KB, broker-dependent); binary YAML must be serialised |
| HTTP endpoint | `http://schema-server:8080/schemas/{app}.yaml` | No size limit, versioned endpoints, caching headers | Requires running an HTTP server; apps need network access beyond the broker |
| Local file | `file:///etc/cosalette/schemas/{app}.yaml` | Fastest load, works fully offline, easiest for CI | No remote update; manual file distribution |

**Settings integration — the app's Pydantic settings class:**

```python
from typing import Literal

from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    schema_source: str = "mqtt://cosalette/schema/{app}"
    schema_enforcement: Literal["strict", "warn", "off"] = "warn"
    schema_reload_enabled: bool = True
```

The `{app}` placeholder is resolved at runtime from `App.name`. The `schema_source`
URI scheme selects the loader implementation.

#### Framework Enforcement Sketch

**Schema loading during `on_configure` (ADR-023).**

The framework loads the schema _before_ device tasks start. This ensures that
capability enforcement (UC1) and payload validation (UC3) are active from the first
publish cycle:

```python
from cosalette._schema import SchemaLoader, SchemaRegistry


class _SchemaLifecycle:
    """Manages schema loading, validation, and hot-reload."""

    def __init__(self, app_name: str, settings: AppSettings) -> None:
        self._app_name = app_name
        self._settings = settings
        self._registry: SchemaRegistry | None = None
        self._loader = SchemaLoader.for_uri(
            settings.schema_source.format(app=app_name),
        )

    async def load(self) -> SchemaRegistry | None:
        """Load the schema during on_configure.

        Returns None if the source is unreachable and enforcement is not
        strict — the app starts without schema validation.
        """
        try:
            raw = await self._loader.fetch()
            self._registry = SchemaRegistry.from_asyncapi(raw)
            return self._registry
        except SchemaSourceUnavailable:
            if self._settings.schema_enforcement == "strict":
                raise  # fatal — refuse to start without schema
            logger.warning(
                "Schema source '%s' unreachable; proceeding without "
                "schema enforcement",
                self._settings.schema_source,
            )
            return None

    async def reload(self) -> ReloadResult:
        """Hot-reload the schema from the configured source.

        Called by the built-in reload command handler.
        """
        previous = self._registry
        new_registry = SchemaRegistry.from_asyncapi(
            await self._loader.fetch(),
        )
        self._registry = new_registry
        violations = self._revalidate_current_registrations(new_registry)
        return ReloadResult(
            previous_version=previous.version if previous else None,
            new_version=new_registry.version,
            violations=violations,
        )

    def _revalidate_current_registrations(
        self,
        registry: SchemaRegistry,
    ) -> list[str]:
        """Re-run capability + mandatory topic validation against the
        current device snapshot. Returns violation messages."""
        from cosalette._introspect import build_registry_snapshot

        snapshot = build_registry_snapshot(self._app_name)
        violations: list[str] = []
        violations.extend(validate_capabilities(registry, snapshot))
        violations.extend(validate_mandatory_topics(registry, snapshot))
        return violations
```

**Built-in reload command handler.**

The framework registers a command handler at `{app}/schema/reload` automatically —
no app code is required. This follows the same pattern as the built-in health check
(ADR-028):

```python
# Registered internally by the framework during App.__init__
@app.command("schema", subtopic="reload")
async def _handle_schema_reload(ctx: DeviceContext, payload: dict) -> None:
    """Built-in handler: reload schema from configured source."""
    result = await ctx.app._schema_lifecycle.reload()
    await ctx.publish_state({
        "schema_version": result.new_version,
        "previous_version": result.previous_version,
        "violations": result.violations,
        "reloaded_at": datetime.utcnow().isoformat(),
    })
    if result.violations:
        logger.warning(
            "Schema reload found %d violation(s) against new schema v%s: %s",
            len(result.violations),
            result.new_version,
            "; ".join(result.violations),
        )
```

**Fleet-wide broadcast reload.**

A message published to `cosalette/schema/update` triggers all apps to re-pull their
schema. Each app subscribes to this broadcast topic at startup:

```python
# Framework-internal subscription
async def _on_schema_broadcast(self, payload: bytes) -> None:
    """Handle fleet-wide schema update broadcast."""
    msg = orjson.loads(payload)
    target_app = msg.get("app")  # None means all apps
    if target_app is not None and target_app != self._app_name:
        return
    await self._schema_lifecycle.reload()
```

#### MQTT Message Examples

**1. Retained schema document on MQTT:**

```text
Topic:   cosalette/schema/vito2mqtt
QoS:     1
Retain:  true
Payload: <raw YAML bytes of the AsyncAPI 3.0.0 document>
```

**2. Reload command targeting a specific app:**

```text
Topic:   vito2mqtt/schema/set/reload
QoS:     1
Retain:  false
Payload: {}
```

**3. Fleet-wide schema update broadcast:**

```text
Topic:   cosalette/schema/update
QoS:     1
Retain:  false
Payload: {"app": null, "reason": "network schema v2.1.0 deployed"}
```

A `null` app field means "all apps reload". A specific app name targets one app:

```text
Payload: {"app": "vito2mqtt", "reason": "vito2mqtt schema updated to 1.2.0"}
```

**4. Schema status response after reload:**

```text
Topic:   vito2mqtt/schema/state
QoS:     1
Retain:  true
Payload:
{
  "schema_version": "1.2.0",
  "previous_version": "1.1.0",
  "violations": [],
  "reloaded_at": "2026-04-08T15:30:00Z"
}
```

**5. Schema status response with violations after reload:**

```text
Topic:   vito2mqtt/schema/state
QoS:     1
Retain:  true
Payload:
{
  "schema_version": "2.0.0",
  "previous_version": "1.2.0",
  "violations": [
    "device 'motion_sensor' tagged 'battery_powered' but missing required channel 'vito2mqtt/motion_sensor/battery'",
    "mandatory app topic 'vito2mqtt/diagnostics' has no registration"
  ],
  "reloaded_at": "2026-04-08T15:30:00Z"
}
```

#### Edge Cases

1. **Schema source unreachable at startup.**
   - `strict` mode: fatal — `SchemaSourceUnavailable` raised, app refuses to start.
     This is correct for CI and development where schema compliance is mandatory.
   - `warn` mode: log warning, proceed without schema enforcement. The app is
     functional but unvalidated. A subsequent reload command can activate enforcement
     once the source becomes available.
   - `off` mode: no attempt to load schema. No error, no warning.

2. **Schema too large for MQTT retained message.**
   Brokers typically limit retained messages to 256 KB (Mosquitto default). A large
   AsyncAPI document with many devices may exceed this. Mitigation: the schema loader
   detects the URI scheme and falls back to HTTP if MQTT fetch returns an error or
   truncated payload. Alternatively, the schema can use `$ref` to external files,
   keeping the MQTT-hosted root document small.

3. **Concurrent reload during telemetry cycle.**
   The reload replaces the `SchemaRegistry` atomically (single reference swap). In
   Python's GIL-protected runtime, a telemetry handler mid-publish will either see
   the old or new registry, never a partially-constructed one. Validators that are
   already executing against the old schema complete with that schema; the next publish
   uses the new one.

4. **Partial schema (missing device definitions).**
   If the schema defines 3 of 5 devices the app registers, the remaining 2 devices are
   unvalidated — they publish without payload checks. This is intentional for
   incremental schema adoption. A future "strict completeness" mode could require the
   schema to cover all registered devices.

5. **Schema source changes between restarts.**
   If the settings change `schema_source` from `file://` to `mqtt://` between
   deployments, the new loader is selected at startup. No migration is needed — the
   schema content is the same regardless of transport.

---

### 3.5 UC5: Schema Migration and Grace Periods (COS-5hx.5)

#### Problem

Schemas evolve. A new version of the network schema adds a mandatory `diagnostics`
topic to every app, or tightens a payload shape from `additionalProperties: true` to
`additionalProperties: false`. Running apps must handle upgrades without breaking —
a schema change should not simultaneously brick every cosalette app in the fleet.

Concrete scenario: schema v1.0 requires `temperature/state` with
`{temperature: number}`. Schema v2.0 adds a mandatory `unit` field:
`{temperature: number, unit: string}`. If v2.0 is deployed fleet-wide at 14:00 and
three apps have not been updated to include the `unit` field, they all fail validation
simultaneously. There is no grace mechanism — compliance is binary.

#### Schema Definition

**Schema versioning and migration declarations:**

```yaml
asyncapi: 3.0.0
info:
  title: vito2mqtt
  version: 2.0.0
x-cosalette-enforcement:
  mode: strict
  hooks:
    on_configure: true
    on_publish: true
  migration:
    previous_version: "1.0.0"
    additions:
      - channel: temperatureState
        field: "payload.properties.unit"
        required_from: "2026-06-01"
        grace_period: "30d"
        description: "Temperature readings must include unit field"
      - channel: appDiagnostics
        required_from: "2026-07-01"
        grace_period: "14d"
        description: "All apps must publish diagnostics topic"
    deprecations:
      - channel: legacyTemperatureRaw
        deprecated_from: "2026-05-01"
        removed_in: "3.0.0"
        description: "Use temperatureState instead"
```

**Grace period semantics:**

A grace period defines a temporal window between "schema says required" and
"enforcement treats it as required". During the grace window, the enforcement
behaviour follows a three-phase lifecycle:

| Phase | Condition | Behaviour |
|-------|-----------|-----------|
| `warning` | Current date < `required_from` | Log informational warning; validation passes |
| `soft_failure` | Current date ≥ `required_from` AND within grace period | Log error-level message; validation passes; status reports non-compliance |
| `hard_failure` | Current date > `required_from` + grace period | Validation fails; `strict` mode blocks startup, `warn` mode logs error |

**Alternative: relative grace periods.**

When an absolute date is not practical (e.g., apps update on different schedules),
the grace period can be expressed relative to when the app first loads the new schema:

```yaml
additions:
  - channel: batteryState
    grace_period: "30d"  # 30 days from first schema load
    # No required_from — grace starts when the app sees schema v2.0
```

The framework records the "first-seen" timestamp for each schema version in a local
state file (`~/.cosalette/schema_state.json`), ensuring the grace period is consistent
across restarts.

#### Framework Enforcement Sketch

**Migration validator:**

```python
from datetime import date, timedelta


@dataclass
class GracePeriodStatus:
    channel: str
    phase: Literal["warning", "soft_failure", "hard_failure"]
    required_from: date | None
    grace_expires: date | None
    message: str


class MigrationValidator:
    """Evaluates migration rules against current date and app state."""

    def __init__(
        self,
        schema: SchemaRegistry,
        state_file: Path = Path.home() / ".cosalette" / "schema_state.json",
    ) -> None:
        self._schema = schema
        self._state = self._load_state(state_file)

    def evaluate_additions(
        self,
        snapshot: dict[str, Any],
        today: date | None = None,
    ) -> list[GracePeriodStatus]:
        """Check each addition rule against the current date."""
        today = today or date.today()
        results: list[GracePeriodStatus] = []

        for addition in self._schema.migration.additions:
            has_channel = self._channel_satisfied(addition.channel, snapshot)

            if has_channel:
                continue  # already compliant — no action needed

            required_from = self._resolve_required_date(addition)
            grace_expires = self._resolve_grace_expiry(addition, required_from)

            if today < required_from:
                phase = "warning"
                msg = (
                    f"Schema v{self._schema.version} will require "
                    f"'{addition.channel}' from {required_from}. "
                    f"Consider adding it before the deadline."
                )
            elif grace_expires and today <= grace_expires:
                phase = "soft_failure"
                days_left = (grace_expires - today).days
                msg = (
                    f"'{addition.channel}' is required by schema "
                    f"v{self._schema.version} but missing. Grace period: "
                    f"{days_left} day(s) remaining (expires {grace_expires})."
                )
            else:
                phase = "hard_failure"
                msg = (
                    f"'{addition.channel}' is required by schema "
                    f"v{self._schema.version} and grace period has expired. "
                    f"App is non-compliant."
                )

            results.append(
                GracePeriodStatus(
                    channel=addition.channel,
                    phase=phase,
                    required_from=required_from,
                    grace_expires=grace_expires,
                    message=msg,
                )
            )

        return results

    def _resolve_required_date(self, addition) -> date:
        """Resolve absolute or relative required date."""
        if addition.required_from:
            return date.fromisoformat(addition.required_from)
        # Relative grace: starts from first-seen date of this schema version
        first_seen = self._state.get(
            f"first_seen_{self._schema.version}",
            date.today().isoformat(),
        )
        return date.fromisoformat(first_seen)

    def _resolve_grace_expiry(self, addition, required_from: date) -> date | None:
        """Compute grace period expiry from required_from + grace_period."""
        if not addition.grace_period:
            return None
        days = self._parse_duration_days(addition.grace_period)
        return required_from + timedelta(days=days)

    @staticmethod
    def _parse_duration_days(duration: str) -> int:
        """Parse '30d' → 30, '2w' → 14."""
        if duration.endswith("d"):
            return int(duration[:-1])
        if duration.endswith("w"):
            return int(duration[:-1]) * 7
        raise ValueError(f"Unsupported duration format: {duration}")
```

**Deprecation lifecycle handler:**

```python
def evaluate_deprecations(
    self,
    snapshot: dict[str, Any],
    today: date | None = None,
) -> list[DeprecationStatus]:
    """Check deprecated channels — warn if still in use."""
    today = today or date.today()
    results: list[DeprecationStatus] = []

    for dep in self._schema.migration.deprecations:
        is_registered = self._channel_satisfied(dep.channel, snapshot)
        if not is_registered:
            continue  # not using the deprecated channel — good

        deprecated_from = date.fromisoformat(dep.deprecated_from)
        if today >= deprecated_from:
            results.append(
                DeprecationStatus(
                    channel=dep.channel,
                    deprecated_from=deprecated_from,
                    removed_in=dep.removed_in,
                    message=(
                        f"Channel '{dep.channel}' is deprecated since "
                        f"{dep.deprecated_from} and will be removed in "
                        f"v{dep.removed_in}. {dep.description}"
                    ),
                )
            )

    return results
```

**Integration with `on_configure` and reload.**

Migration validation runs after capability and mandatory topic checks:

```python
@app.on_configure
def enforce_schema_with_migration(settings: AppSettings) -> None:
    schema = load_schema(settings.schema_path)
    snapshot = build_registry_snapshot(app)

    # Phase 1: standard enforcement (UC1, UC2)
    violations = validate_capabilities(schema, snapshot)
    violations.extend(validate_mandatory_topics(schema, snapshot))

    # Phase 2: migration enforcement (UC5)
    migration = MigrationValidator(schema)
    addition_statuses = migration.evaluate_additions(snapshot)
    deprecation_statuses = migration.evaluate_deprecations(snapshot)

    hard_failures = [s for s in addition_statuses if s.phase == "hard_failure"]
    soft_failures = [s for s in addition_statuses if s.phase == "soft_failure"]
    warnings = [s for s in addition_statuses if s.phase == "warning"]

    for w in warnings:
        logger.info(w.message)
    for sf in soft_failures:
        logger.error(sf.message)
    for dep in deprecation_statuses:
        logger.warning(dep.message)

    # Hard failures + standard violations are fatal in strict mode
    all_fatal = violations + [hf.message for hf in hard_failures]
    if all_fatal and settings.schema_enforcement == "strict":
        raise SchemaViolation(all_fatal)
```

#### MQTT Message Examples

**1. Schema status with migration warnings (during grace period):**

```text
Topic:   vito2mqtt/schema/state
QoS:     1
Retain:  true
Payload:
{
  "schema_version": "2.0.0",
  "enforcement_mode": "warn",
  "compliance": "partial",
  "migration": {
    "additions": [
      {
        "channel": "temperatureState",
        "field": "payload.properties.unit",
        "phase": "soft_failure",
        "grace_expires": "2026-07-01",
        "days_remaining": 84,
        "message": "'unit' field required from 2026-06-01. Grace: 84 days remaining."
      },
      {
        "channel": "appDiagnostics",
        "phase": "warning",
        "required_from": "2026-07-01",
        "message": "Diagnostics topic required from 2026-07-01. Consider adding it."
      }
    ],
    "deprecations": [
      {
        "channel": "legacyTemperatureRaw",
        "deprecated_from": "2026-05-01",
        "removed_in": "3.0.0",
        "message": "Deprecated since 2026-05-01. Use temperatureState instead."
      }
    ]
  }
}
```

**2. Hard failure after grace period expiry (strict mode refuses to start):**

```text
$ uv run python -m vito2mqtt
cosalette.SchemaViolation: 1 migration violation(s):

  • 'appDiagnostics' is required by schema v2.0.0 and grace period has
    expired (was 2026-07-15). App is non-compliant.

    Fix: add a telemetry handler publishing to 'vito2mqtt/diagnostics'
    with the required payload shape, or extend the grace period in the
    schema.
```

**3. Reload triggers re-evaluation of migration status:**

```text
Topic:   vito2mqtt/schema/set/reload
Payload: {}

→ App reloads schema v2.0.0, re-runs migration validation.
→ If the app gained the required handler since last validation, the addition
  status moves from soft_failure to compliant (no entry in migration output).
```

#### Edge Cases

1. **Schema downgrade (v2 → v1).**
   The framework allows loading an older schema version. Migration rules from v2 are
   discarded — they reference additions/deprecations that do not exist in v1. The
   "first-seen" state for v2 is preserved in the state file in case v2 is re-deployed
   later (the grace period resumes where it left off, not from zero).

2. **Conflicting versions across the network.**
   Two schema sources disagree — one serves v1.2.0, another serves v2.0.0. Each app
   loads from its configured `schema_source`, so conflicts are invisible to
   individual apps. The network-level monitoring tool (UC7) detects the discrepancy
   by comparing `schema_version` fields in each app's `schema/state` topic.

3. **App partially compliant.**
   An app satisfies 5 of 7 required channels from the new schema. The 5 satisfied
   channels pass validation; the 2 missing channels each have their own grace period
   evaluation. Partial compliance is reported in the schema status — the app is not
   treated as fully failing or fully passing.

4. **Grace period across restarts.**
   The "first-seen" timestamp is persisted in `~/.cosalette/schema_state.json`. If
   the file is deleted (container rebuild, new deployment), the grace period resets.
   For production environments, the state file should be mounted as a persistent
   volume or the schema should use absolute `required_from` dates instead of relative
   grace periods.

5. **Simultaneous migration and payload validation.**
   Schema v2.0 adds a new required field to an existing payload. During the grace
   period, the `on_configure` migration validator shows `soft_failure` (missing field
   in schema), but the `on_publish` payload validator may still be using v1.0's schema
   (which doesn't require the field). After reload to v2.0, payload validation
   activates the new field requirement. This two-phase transition is intentional — it
   prevents immediate hard failures from a schema swap.

6. **Time zone ambiguity.**
   `required_from` dates are evaluated against `date.today()` in the app's local time
   zone. For globally distributed fleets, UTC dates are recommended in the schema.
   The framework does not enforce a time zone — operators choose consistency by
   convention.

---

### 3.6 UC6: Developer Tooling (COS-5hx.6)

#### Problem

Developers have no automated way to verify that their cosalette app's registrations
match a schema, generate a schema from an existing app, or get feedback during
development. Schema compliance is checked manually by reading YAML files and comparing
them against code. CI pipelines have no schema validation step. The gap between "code
compiles and tests pass" and "app conforms to the MQTT contract" is uncovered.

Concrete scenario: a developer adds a `humidity` telemetry handler to `vito2mqtt` but
forgets to update the schema. The CI pipeline passes (unit tests don't check schema
compliance). The schema and the app drift apart silently. Six months later, a new team
member trusts the schema as documentation and builds a consumer that expects only the
topics listed in the schema — missing the `humidity` data entirely.

#### Schema Definition

Developer tooling operates on the same AsyncAPI 3.0.0 + `x-cosalette-*` schema used
by the runtime (UC1–UC5). No additional schema format is needed. The tooling surfaces
are:

1. **`cosalette schema dump`** — generates a schema from the app's current registrations
2. **`cosalette schema check`** — validates an app against a schema
3. **`cosalette schema diff`** — compares two schemas or a schema vs. app snapshot
4. **Code generation** — generates handler stubs from a schema

**JSON Schema for `x-cosalette-*` extension properties (enables IDE validation):**

```yaml
# x-cosalette-extensions.schema.json (referenced by IDE for YAML validation)
$schema: "https://json-schema.org/draft/2020-12/schema"
type: object
properties:
  x-cosalette-enforcement:
    type: object
    properties:
      mode:
        type: string
        enum: [strict, warn, "off"]
      hooks:
        type: object
        properties:
          on_configure: { type: boolean }
          on_publish: { type: boolean }
  x-cosalette-requires:
    type: object
    properties:
      tag: { type: string }
  x-cosalette-archetype:
    type: string
    enum: [telemetry, command, device]
  x-cosalette-coalescing-group:
    type: string
```

#### Framework Enforcement Sketch

**`cosalette schema dump` — introspect app and generate AsyncAPI:**

```python
from cosalette._introspect import build_registry_snapshot


def dump_schema(app_name: str) -> str:
    """Generate AsyncAPI 3.0.0 YAML from app's current registrations.

    Leverages build_registry_snapshot() to inspect all registered devices,
    telemetry handlers, and command handlers, then emits an AsyncAPI document.
    """
    snapshot = build_registry_snapshot(app_name)
    return generate_asyncapi(snapshot)


def generate_asyncapi(snapshot: dict) -> str:
    """Transform a registry snapshot into AsyncAPI 3.0.0 YAML."""
    doc = {
        "asyncapi": "3.0.0",
        "info": {
            "title": snapshot["app"]["name"],
            "version": "0.0.0-snapshot",
        },
        "channels": {},
        "operations": {},
    }

    for device in snapshot.get("devices", []):
        _add_device_channels(doc, snapshot["app"]["name"], device)
    for telemetry in snapshot.get("telemetry", []):
        _add_telemetry_channel(doc, snapshot["app"]["name"], telemetry)
    for command in snapshot.get("commands", []):
        _add_command_channel(doc, snapshot["app"]["name"], command)

    return yaml.dump(doc, default_flow_style=False, sort_keys=False)


def _add_telemetry_channel(doc: dict, app_name: str, telemetry: dict) -> None:
    """Add a telemetry channel to the AsyncAPI document."""
    name = telemetry["name"]
    channel_id = f"{name}State"
    doc["channels"][channel_id] = {
        "address": f"{app_name}/{name}/state",
        "messages": {
            "stateMessage": {
                "payload": {
                    "type": "object",
                    # Placeholder — actual schema needs manual refinement
                    "description": f"Auto-generated from {name} telemetry handler",
                },
            },
        },
    }
    doc["operations"][f"publish{name.title()}State"] = {
        "action": "send",
        "channel": {"$ref": f"#/channels/{channel_id}"},
        "x-cosalette-archetype": "telemetry",
    }
```

**CLI entry point:**

```text
$ cosalette schema dump > vito2mqtt-schema.yaml
# or equivalently:
$ uv run python -m cosalette schema dump > vito2mqtt-schema.yaml
```

**Comparing contract vs. actual:**

```text
$ diff schema/vito2mqtt.yaml <(cosalette schema dump)
3c3
<   version: 1.2.0
---
>   version: 0.0.0-snapshot
12a13,20
>   humidityState:
>     address: "vito2mqtt/humidity/state"
>     ...
```

The diff reveals that the `humidity` channel exists in the app but not in the schema.

**`cosalette schema check` — validate app against schema:**

```python
def check_schema(app_name: str, schema_path: str) -> list[SchemaCheckResult]:
    """Validate app registrations against a schema file.

    Returns a list of results: compliant channels, missing channels,
    and extra channels (in app but not in schema).
    """
    schema = SchemaRegistry.from_asyncapi_file(schema_path)
    snapshot = build_registry_snapshot(app_name)
    results: list[SchemaCheckResult] = []

    # Channels in schema but not in app
    for channel_id, channel_def in schema.channels.items():
        if not _channel_in_snapshot(channel_def, snapshot):
            results.append(
                SchemaCheckResult(
                    channel=channel_id,
                    status="missing",
                    message=f"Schema requires '{channel_id}' but app has no registration",
                )
            )

    # Channels in app but not in schema
    for registration in _all_registrations(snapshot):
        if not _registration_in_schema(registration, schema):
            results.append(
                SchemaCheckResult(
                    channel=registration.name,
                    status="extra",
                    message=f"App registers '{registration.name}' but schema has no definition",
                )
            )

    return results
```

**Code generation from schema — handler stubs:**

```python
def generate_stubs(schema_path: str) -> str:
    """Generate Python handler stubs from a schema definition.

    Generates skeleton @app.telemetry, @app.command, and @app.device
    decorators for each device in the schema.
    """
    schema = SchemaRegistry.from_asyncapi_file(schema_path)
    lines: list[str] = []

    for channel_id, channel_def in schema.channels.items():
        archetype = channel_def.extensions.get("x-cosalette-archetype", "device")
        device_name = _extract_device_name(channel_def.address)

        if archetype == "telemetry":
            interval = channel_def.extensions.get("x-cosalette-interval", 60)
            lines.append(f'@app.telemetry("{device_name}", interval={interval})')
            lines.append(f"async def {device_name}_handler(ctx: DeviceContext) -> dict:")
            lines.append(f'    """Publish {device_name} state."""')
            lines.append(f"    # TODO: implement {device_name} telemetry")
            lines.append(f"    return {_schema_to_stub_dict(channel_def.payload)}")
            lines.append("")
        elif archetype == "command":
            lines.append(f'@app.command("{device_name}")')
            lines.append(f"async def {device_name}_handler(ctx: DeviceContext, payload: dict) -> None:")
            lines.append(f'    """Handle {device_name} command."""')
            lines.append(f"    # TODO: implement {device_name} command handler")
            lines.append(f"    pass")
            lines.append("")
        elif archetype == "device":
            lines.append(f'@app.device("{device_name}")')
            lines.append(f"async def {device_name}_handler(ctx: DeviceContext) -> None:")
            lines.append(f'    """Bidirectional handler for {device_name}."""')
            lines.append(f"    # TODO: implement {device_name} device logic")
            lines.append(f"    async for cmd in ctx.commands():")
            lines.append(f"        pass  # handle command")
            lines.append("")

    return "\n".join(lines)
```

**Example: schema defines `temperature` telemetry → generated stub:**

```python
@app.telemetry("temperature", interval=60)
async def temperature_handler(ctx: DeviceContext) -> dict:
    """Publish temperature state."""
    # TODO: implement temperature telemetry
    return {"temperature": 0.0, "unit": "celsius"}
```

**Feasibility note:** topic structure, payload shape, and decorator wiring are
highly generatable. Business logic (how to read a sensor, transform data, handle
errors) is inherently non-generatable. Code generation provides scaffolding, not
implementation.

**CI validation — pytest plugin:**

```python
# conftest.py or pytest plugin
import pytest

from cosalette._schema import SchemaRegistry, validate_capabilities, validate_mandatory_topics
from cosalette._introspect import build_registry_snapshot


@pytest.fixture
def schema_registry(request):
    """Load schema from marker or default path."""
    marker = request.node.get_closest_marker("schema_compliant")
    schema_path = marker.args[0] if marker and marker.args else "schema/app.yaml"
    return SchemaRegistry.from_asyncapi_file(schema_path)


@pytest.mark.schema_compliant("schema/vito2mqtt.yaml")
def test_app_matches_schema(app, schema_registry):
    """Verify that the app's registrations match the schema."""
    snapshot = build_registry_snapshot(app)
    violations = validate_capabilities(schema_registry, snapshot)
    violations.extend(validate_mandatory_topics(schema_registry, snapshot))
    assert violations == [], f"Schema violations: {violations}"
```

**CI pipeline integration:**

```yaml
# In CI workflow (e.g., GitHub Actions)
- name: Schema compliance check
  run: |
    uv run cosalette schema check --schema schema/vito2mqtt.yaml
    # or via task:
    # task schema:validate
```

**IDE integration — extension property validation in editors:**

The JSON Schema for `x-cosalette-*` extension properties
(`x-cosalette-extensions.schema.json`) enables YAML language servers (e.g.,
`yaml-language-server` in VS Code) to validate AsyncAPI documents with cosalette
extensions. Configuration:

```json
// .vscode/settings.json
{
  "yaml.schemas": {
    "schema/x-cosalette-extensions.schema.json": "schema/*.yaml"
  }
}
```

**Language server feasibility assessment.**

A dedicated cosalette language server (providing autocomplete for device names,
cross-validation between schema and Python code) would require:

- Custom LSP implementation (~2000+ lines of Python)
- AST parsing of both YAML schema and Python source
- Ongoing maintenance as the schema format evolves

Assessment: **high effort, moderate benefit.** The JSON Schema approach covers 80% of
the IDE validation value (structural correctness, property types, enum validation) at
5% of the implementation cost. A full language server is future scope — revisit only
if developer adoption data shows schema authoring friction as the primary barrier.

#### MQTT Message Examples

**1. Schema dump output (generated from app introspection):**

```text
$ cosalette schema dump
asyncapi: 3.0.0
info:
  title: vito2mqtt
  version: 0.0.0-snapshot
channels:
  temperatureState:
    address: vito2mqtt/temperature/state
    messages:
      stateMessage:
        payload:
          type: object
          description: Auto-generated from temperature telemetry handler
  valveState:
    address: vito2mqtt/valve/state
    messages:
      stateMessage:
        payload:
          type: object
          description: Auto-generated from valve device handler
operations:
  publishTemperatureState:
    action: send
    channel:
      $ref: '#/channels/temperatureState'
    x-cosalette-archetype: telemetry
```

**2. Schema check output (CLI):**

```text
$ cosalette schema check --schema schema/vito2mqtt.yaml
Schema: schema/vito2mqtt.yaml (v1.2.0)
App:    vito2mqtt

✓ temperatureState    — compliant
✓ valveState          — compliant
✗ motionSensorBattery — MISSING (schema requires, app missing)
⚠ humidityState       — EXTRA (app registers, schema missing)

Result: 1 missing, 1 extra, 2 compliant
Exit code: 1
```

**3. Generated stubs output:**

```text
$ cosalette schema generate-stubs --schema schema/vito2mqtt.yaml
# Generated from schema/vito2mqtt.yaml v1.2.0

@app.telemetry("temperature", interval=60)
async def temperature_handler(ctx: DeviceContext) -> dict:
    """Publish temperature state."""
    # TODO: implement temperature telemetry
    return {"temperature": 0.0, "unit": "celsius"}

@app.device("valve")
async def valve_handler(ctx: DeviceContext) -> None:
    """Bidirectional handler for valve."""
    # TODO: implement valve device logic
    async for cmd in ctx.commands():
        pass  # handle command
```

#### Edge Cases

1. **`--dump-schema` with dynamic `on_configure` hooks.**
   Some apps register devices conditionally in `on_configure` (e.g., based on
   hardware detection). The `dump` command runs the full `on_configure` phase to
   capture the complete registry. If `on_configure` depends on hardware that is not
   present (CI environment), the dump may produce an incomplete schema. Mitigation:
   provide a `--mock-configure` flag that loads a fixture configuration, or document
   that `dump` should be run on target hardware.

2. **Schema with forward-references.**
   A schema references a device the app has not built yet (planned for a future
   release). `schema check` reports it as "missing" — this is correct and expected.
   The developer can suppress specific channels via a `.cosalette-schema-ignore` file:

   ```text
   # Planned for v2.0 — suppress until implemented
   airQualityState
   ```

3. **Code generation overwrites existing handlers.**
   `generate-stubs` writes to stdout by default — it never overwrites files. If piped
   to a file (`> handlers.py`), the developer explicitly chooses to overwrite. A
   `--output` flag with `--no-clobber` semantics can be added if needed.

4. **Schema and test fixture drift.**
   The pytest plugin validates against the schema at test time. If the schema is
   updated but test fixtures still use old payload shapes, tests pass (the app _code_
   matches the schema) but fixture-driven tests may fail independently. Recommendation:
   generate test fixtures from the schema as well (a `cosalette schema fixtures`
   command could produce sample payloads from JSON Schema).

5. **Multiple schemas for one app.**
   An app may have a "development" schema (permissive, `additionalProperties: true`)
   and a "production" schema (strict). The `--schema` flag selects which to validate
   against. The CI pipeline should validate against the production schema; local
   development uses the permissive one.

---

### 3.7 UC7: Inter-App Coordination (COS-5hx.6)

#### Problem

Each cosalette app validates against its own schema in isolation. There is no mechanism
to verify that the _network_ of apps collectively satisfies the system's expectations.
If the smart home requires a temperature sensor in zone `living_room`, no single app's
schema can express or enforce that requirement — it depends on which apps are running
and what devices they provide.

Concrete scenario: a smart home runs `vito2mqtt` (heating), `airthings2mqtt` (air
quality), and `shelly2mqtt` (relays). A network-level configuration expects
`vito2mqtt` to publish `temperature/state` and `airthings2mqtt` to publish
`airquality/state`. If `airthings2mqtt` is stopped for maintenance, no app detects
that the `airquality/state` topic is no longer being published. Monitoring dashboards
show stale data without any alert.

#### Schema Definition

**Network-level schema — an AsyncAPI document spanning all apps:**

```yaml
asyncapi: 3.0.0
info:
  title: Smart Home MQTT Network
  version: 1.0.0
  description: |
    Network-level schema defining ALL expected topics across all cosalette
    apps. Used by the monitoring tool to verify collective compliance.

x-cosalette-enforcement:
  mode: warn
  network_level: true

channels:
  vitoTemperature:
    address: "vito2mqtt/temperature/state"
    x-cosalette-expected-app: vito2mqtt
    messages:
      temperatureReading:
        payload:
          type: object
          required: [temperature, unit]
          properties:
            temperature: { type: number }
            unit: { type: string, enum: [celsius, fahrenheit] }

  airthingsAirQuality:
    address: "airthings2mqtt/airquality/state"
    x-cosalette-expected-app: airthings2mqtt
    messages:
      airQualityReading:
        payload:
          type: object
          required: [co2, voc, humidity]
          properties:
            co2: { type: integer }
            voc: { type: integer }
            humidity: { type: number }

  shellyRelay1:
    address: "shelly2mqtt/relay1/state"
    x-cosalette-expected-app: shelly2mqtt
    messages:
      relayState:
        payload:
          type: object
          required: [on]
          properties:
            on: { type: boolean }

  vitoValve:
    address: "vito2mqtt/valve/state"
    x-cosalette-expected-app: vito2mqtt
    x-cosalette-archetype: device
    messages:
      valveState:
        payload:
          type: object
          required: [position]
          properties:
            position: { type: integer, minimum: 0, maximum: 100 }
```

**Per-app schema status publication.**

Each app publishes its compliance report on a well-known topic:

```yaml
# Implicitly added by the framework when schema enforcement is enabled
channels:
  schemaStatus:
    address: "{appName}/schema/status"
    messages:
      statusReport:
        payload:
          type: object
          required: [app, schema_version, compliant, channels]
          properties:
            app: { type: string }
            schema_version: { type: string }
            compliant: { type: boolean }
            channels:
              type: array
              items:
                type: object
                properties:
                  name: { type: string }
                  status: { type: string, enum: [compliant, missing, extra, grace_period] }
```

#### Framework Enforcement Sketch

**Per-app: publish schema status.**

Each cosalette app publishes its compliance status to `{app}/schema/status` as a
retained message. This happens automatically after `on_configure` validation and
after each schema reload:

```python
async def _publish_schema_status(self) -> None:
    """Publish the app's schema compliance status."""
    if self._schema_registry is None:
        return

    snapshot = build_registry_snapshot(self._app_name)
    channel_statuses = self._evaluate_all_channels(snapshot)

    status = {
        "app": self._app_name,
        "schema_version": self._schema_registry.version,
        "compliant": all(c["status"] == "compliant" for c in channel_statuses),
        "channels": channel_statuses,
        "reported_at": datetime.utcnow().isoformat(),
    }

    await self._mqtt.publish(
        f"{self._app_name}/schema/status",
        orjson.dumps(status),
        retain=True,
        qos=1,
    )
```

**Network monitor: aggregate compliance from all apps.**

A standalone monitoring tool (not a cosalette app itself — it is a lightweight
subscriber) watches `+/schema/status` and compares against the network schema:

```python
class NetworkSchemaMonitor:
    """Subscribes to +/schema/status and aggregates compliance."""

    def __init__(self, network_schema_path: str) -> None:
        self._network_schema = SchemaRegistry.from_asyncapi_file(
            network_schema_path,
        )
        self._app_statuses: dict[str, dict] = {}

    async def on_status_message(self, topic: str, payload: bytes) -> None:
        """Handle incoming schema/status message from any app."""
        # topic format: {app}/schema/status
        app_name = topic.split("/")[0]
        status = orjson.loads(payload)
        self._app_statuses[app_name] = status
        await self._evaluate_network_compliance()

    async def _evaluate_network_compliance(self) -> None:
        """Check all expected channels against reported app statuses."""
        gaps: list[str] = []
        covered: list[str] = []

        for channel_id, channel_def in self._network_schema.channels.items():
            expected_app = channel_def.extensions.get("x-cosalette-expected-app")
            if expected_app is None:
                continue

            app_status = self._app_statuses.get(expected_app)
            if app_status is None:
                gaps.append(
                    f"Channel '{channel_id}' expects app '{expected_app}' "
                    f"but no schema/status received from it (app offline?)"
                )
                continue

            # Check if the specific channel is reported as compliant
            channel_found = False
            for ch in app_status.get("channels", []):
                if ch["name"] == channel_id:
                    channel_found = True
                    if ch["status"] != "compliant":
                        gaps.append(
                            f"Channel '{channel_id}' in app '{expected_app}' "
                            f"has status '{ch['status']}' (expected: compliant)"
                        )
                    else:
                        covered.append(channel_id)
                    break

            if not channel_found:
                gaps.append(
                    f"Channel '{channel_id}' expected from app '{expected_app}' "
                    f"but not present in its schema/status report"
                )

        # Publish network compliance summary
        summary = {
            "total_expected": len(self._network_schema.channels),
            "covered": len(covered),
            "gaps": gaps,
            "apps_reporting": list(self._app_statuses.keys()),
            "evaluated_at": datetime.utcnow().isoformat(),
        }

        await self._mqtt.publish(
            "cosalette/network/schema/status",
            orjson.dumps(summary),
            retain=True,
            qos=1,
        )

        if gaps:
            logger.warning(
                "Network schema gaps (%d): %s",
                len(gaps),
                "; ".join(gaps),
            )
```

**Gap detection example output:**

```text
2026-04-08 16:00:00 WARNING cosalette.network_monitor: Network schema gaps (1):
  Channel 'airthingsAirQuality' expects app 'airthings2mqtt' but no
  schema/status received from it (app offline?)
```

#### MQTT Message Examples

**1. Per-app schema status (compliant):**

```text
Topic:   vito2mqtt/schema/status
QoS:     1
Retain:  true
Payload:
{
  "app": "vito2mqtt",
  "schema_version": "1.2.0",
  "compliant": true,
  "channels": [
    {"name": "vitoTemperature", "status": "compliant"},
    {"name": "vitoValve", "status": "compliant"}
  ],
  "reported_at": "2026-04-08T16:00:00Z"
}
```

**2. Per-app schema status (partial compliance):**

```text
Topic:   shelly2mqtt/schema/status
QoS:     1
Retain:  true
Payload:
{
  "app": "shelly2mqtt",
  "schema_version": "1.0.0",
  "compliant": false,
  "channels": [
    {"name": "shellyRelay1", "status": "compliant"},
    {"name": "shellyRelay2", "status": "missing"}
  ],
  "reported_at": "2026-04-08T16:00:00Z"
}
```

**3. Network compliance summary (with gap):**

```text
Topic:   cosalette/network/schema/status
QoS:     1
Retain:  true
Payload:
{
  "total_expected": 4,
  "covered": 3,
  "gaps": [
    "Channel 'airthingsAirQuality' expects app 'airthings2mqtt' but no schema/status received from it (app offline?)"
  ],
  "apps_reporting": ["vito2mqtt", "shelly2mqtt"],
  "evaluated_at": "2026-04-08T16:00:00Z"
}
```

**4. Network compliance summary (all healthy):**

```text
Topic:   cosalette/network/schema/status
QoS:     1
Retain:  true
Payload:
{
  "total_expected": 4,
  "covered": 4,
  "gaps": [],
  "apps_reporting": ["vito2mqtt", "airthings2mqtt", "shelly2mqtt"],
  "evaluated_at": "2026-04-08T16:05:00Z"
}
```

**Relationship to Home Assistant Discovery.**

HA discovery and network schema enforcement are complementary, not competing:

| Concern | HA Discovery | Network Schema |
|---------|-------------|----------------|
| Purpose | Tell HA _how to consume_ topics (entity type, icons, units) | Verify the _structure_ is present and correct |
| Direction | App → HA (consumer-facing) | App → Monitor (operator-facing) |
| Payload | HA-specific configuration (platform, device_class, etc.) | JSON Schema validation, compliance status |
| Scope | Per-entity | Per-channel, per-device, per-app, per-network |
| Enforcement | None (HA ignores malformed discovery) | Configurable (strict/warn/off) |

An app can be fully HA-discoverable but schema-non-compliant (e.g., discovery is
published but the payload shape drifts). Conversely, an app can be schema-compliant
but not publish HA discovery (e.g., a headless data-logging app). The two systems
validate different properties of the same MQTT traffic.

#### Edge Cases

1. **Stale `schema/status` retained messages.**
   When an app stops, its `schema/status` retained message remains on the broker. The
   network monitor sees the app as "reporting" even though it is offline. Mitigation:
   use the app's LWT (`{app}/status → {"online": false}`) to mark its schema status
   as stale. The monitor cross-references `{app}/status` with `{app}/schema/status`
   — if the app is offline, its channels are flagged as "app offline" rather than
   "compliant".

2. **Partial network availability.**
   During startup, not all apps have published their `schema/status` yet. The monitor
   should wait for a configurable settling period (e.g., 60 seconds after its own
   startup) before reporting gaps. During the settling window, missing apps are
   reported as "pending" rather than "gap".

3. **App publishes a subset of expected topics.**
   An app's `schema/status` reports 3 of 5 channels as compliant and 2 as missing.
   The network monitor aggregates this correctly — those 2 channels appear in the
   `gaps` list. No special handling is needed beyond passing through the per-app
   status.

4. **Multiple apps claim the same channel.**
   The network schema assigns `vitoTemperature` to `vito2mqtt`, but a second app
   (`vito2mqtt-dev`) also publishes to `vito2mqtt/temperature/state`. The network
   monitor only checks the _assigned_ app's `schema/status` — the extra publisher
   is invisible to the compliance check. This is intentional: the schema declares
   _expectations_, not _exclusivity_. If exclusivity is needed, a separate
   "topic ownership" policy must be implemented at the broker level (e.g., ACLs).

5. **Network schema version conflicts.**
   If the network schema is updated (v1 → v2) but some apps still validate against
   v1's per-app schema, the network monitor detects the mismatch via `schema_version`
   fields. The monitor can report "app X reports schema v1.0.0 but network expects
   channels from v2.0.0" — prompting the operator to trigger a fleet-wide reload
   (UC4) and wait for grace periods (UC5) to elapse.

6. **Monitor itself goes down.**
   The network monitor is a stateless subscriber — it has no persistent state beyond
   the retained messages on the broker. Restarting the monitor causes it to re-read
   all retained `+/schema/status` messages and rebuild its compliance view. No data
   is lost. For availability, the monitor can be run as a systemd service with
   automatic restart, or as a second cosalette app with its own health reporting.

---

## 4. Architecture Design

### 4.1 Schema Module Design (COS-5hx.8)

#### Module Overview

`cosalette/_schema.py` is the pure data-model module for parsed MQTT schema
definitions. It contains **frozen dataclasses** representing an AsyncAPI 3.0.0 document
with `x-cosalette-*` extensions, plus a lightweight `PayloadValidator` that
pre-compiles JSON Schema validators for runtime payload checks.

Design principles:

- **No I/O.** The module never reads files, opens sockets, or touches the network.
  Loading and resolving a raw AsyncAPI YAML document is the job of the schema _loader_
  (Section 4.2). `_schema.py` only models the _result_ of that parse.
- **Immutable after construction.** Every dataclass uses `frozen=True`, matching the
  pattern established by `_DeviceRegistration`, `_TelemetryRegistration`, and
  `_CommandRegistration` in `_registration.py`. Once a `SchemaRegistry` is built, it
  can be shared freely across async tasks without locks.
- **Thin layer over AsyncAPI.** Field names align with AsyncAPI 3.0.0 terminology
  (channels, operations, messages, bindings) rather than inventing cosalette-specific
  synonyms. The `x-cosalette-*` extensions surface as dedicated fields on the relevant
  dataclass — not as a generic `extensions: dict` bag.

#### Core Data Model

##### `EnforcementConfig`

Maps the `x-cosalette-enforcement` document-level extension. Controls whether schema
validation is fatal (`strict`), advisory (`warn`), or disabled (`off`), and at which
lifecycle hooks validation fires (UC1, UC3).

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class EnforcementConfig:
    """Document-level enforcement settings from ``x-cosalette-enforcement``.

    Defaults match the "safe first use" principle: warn mode is on during
    ``on_configure`` (catching capability violations — UC1, UC2) but off
    during ``on_publish`` (avoiding runtime overhead until opted in — UC3).
    """

    mode: Literal["strict", "warn", "off"] = "warn"
    on_configure: bool = True
    on_publish: bool = False
```

- `mode` governs severity: `strict` raises `SchemaViolation` and blocks startup (or
  publish), `warn` logs and continues, `off` skips validation entirely.
- `on_configure` enables registration-time checks — capability enforcement (UC1) and
  mandatory topic validation (UC2) during the `on_configure` lifecycle phase (ADR-023).
- `on_publish` enables payload validation before each MQTT publish (UC3). Disabled by
  default because it adds per-message overhead; intended for development or CI.

##### `CapabilityRequirement`

Represents a single `x-cosalette-requires` annotation on a channel. A channel with
this extension is _conditionally required_: it applies only to devices carrying the
specified tag (UC1).

```python
@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """Tag-based capability requirement from ``x-cosalette-requires``.

    When attached to a channel, it declares: "every device whose tag set
    includes *tag* must have a registration that covers this channel."
    See ADR-010 (device archetypes) for the tag model.
    """

    tag: str
    description: str | None = None
```

##### `MqttBinding`

Captures the MQTT-specific binding properties defined by the AsyncAPI MQTT Binding
specification. Defaults follow ADR-002's conventions for state topics.

```python
@dataclass(frozen=True, slots=True)
class MqttBinding:
    """MQTT binding properties from the ``bindings.mqtt`` object.

    Defaults reflect ADR-002 conventions: QoS 1 (at-least-once delivery
    for state and availability), retain off (safe default — callers must
    opt in to retained messages).
    """

    qos: int = 1  # 0, 1, or 2
    retain: bool = False
```

##### `ChannelSchema`

The richest dataclass — it aggregates the AsyncAPI channel definition, its resolved
message payload, MQTT bindings, and all `x-cosalette-*` metadata. One `ChannelSchema`
instance exists per named channel in the AsyncAPI document.

```python
@dataclass(frozen=True, slots=True)
class ChannelSchema:
    """Parsed representation of a single AsyncAPI channel.

    ``address_template`` retains parameter placeholders (``{appName}``,
    ``{deviceName}``) for pattern matching. ``address`` holds either the
    same template or a fully-resolved concrete address when parameters
    are not used (e.g. ``vito2mqtt/status``).

    The ``payload_schema`` is the raw JSON Schema dict extracted from
    the channel's message after ``$ref`` resolution. It is passed
    directly to ``jsonschema.Draft7Validator`` by the
    :class:`PayloadValidator`.
    """

    address: str
    address_template: str
    direction: Literal["send", "receive", "both"]
    payload_schema: dict[str, Any] | None = None
    mqtt_binding: MqttBinding = field(default_factory=MqttBinding)
    capability_requirements: list[CapabilityRequirement] = field(
        default_factory=list,
    )
    archetype: Literal["telemetry", "command", "device"] | None = None
    coalescing_group: str | None = None
    message_name: str | None = None
```

- `address` / `address_template` — for parameterised channels
  (`{appName}/{deviceName}/state`) both fields hold the template string. For concrete
  channels (`vito2mqtt/status`) they are identical. The schema loader resolves
  `address` to a concrete value when the channel has no parameters; otherwise
  `address` equals `address_template`.
- `direction` — inferred from the AsyncAPI operations that reference this channel. A
  channel referenced by both a `send` and a `receive` operation gets `"both"`, mapping
  to a `@app.device` registration (ADR-010).
- `payload_schema` — the JSON Schema dict from the channel's message payload, after
  `$ref` resolution. `None` when the channel has no declared payload (e.g. availability
  topics that carry a simple string).
- `capability_requirements` — populated from `x-cosalette-requires`. A channel may
  have zero or more requirements (e.g. a topic required for both `battery_powered`
  and `solar_powered` tags).
- `archetype` — from `x-cosalette-archetype` on the associated operation. Corresponds
  to the ADR-010 archetype model: `telemetry` → `@app.telemetry`, `command` →
  `@app.command`, `device` → `@app.device`.
- `coalescing_group` — from `x-cosalette-coalescing-group` (ADR-018). When set, the
  channel's telemetry publishes are grouped with other channels in the same coalescing
  group.

##### `OperationSchema`

A thin representation of an AsyncAPI operation. Operations are the bridge between the
framework's archetype model and the AsyncAPI channel model — each `@app.telemetry`
decorator maps to a `send` operation, each `@app.command` maps to a `receive`
operation, and each `@app.device` produces both.

```python
@dataclass(frozen=True, slots=True)
class OperationSchema:
    """Parsed representation of a single AsyncAPI operation.

    Operations carry the ``x-cosalette-archetype`` and
    ``x-cosalette-coalescing-group`` extensions because these are
    semantically operation-level concerns (ADR-010, ADR-018).
    """

    action: Literal["send", "receive"]
    channel_ref: str  # channel name key in SchemaRegistry.channels
    archetype: Literal["telemetry", "command", "device"] | None = None
    coalescing_group: str | None = None
    mqtt_binding: MqttBinding = field(default_factory=MqttBinding)
```

##### `SchemaRegistry`

The top-level container. A single `SchemaRegistry` instance holds everything the
framework needs for enforcement (UC1–UC3) and tooling (UC6). It is the public
interface returned by the schema loader.

```python
@dataclass(frozen=True, slots=True)
class SchemaRegistry:
    """Top-level container for a parsed AsyncAPI + x-cosalette-* schema.

    Constructed by the schema loader after YAML parsing, ``$ref``
    resolution, and extension extraction. Immutable after creation —
    safe to share across async tasks without synchronisation.
    """

    app_name: str
    app_version: str
    asyncapi_version: str  # expected: "3.0.0"
    enforcement: EnforcementConfig
    channels: dict[str, ChannelSchema]  # keyed by channel name
    operations: dict[str, OperationSchema]
    component_schemas: dict[str, dict[str, Any]]  # reusable JSON Schema components
    device_names: frozenset[str]  # extracted from channel parameter enums

    # -- Query helpers -------------------------------------------------------

    def channels_for_device(self, device_name: str) -> list[ChannelSchema]:
        """Return all channels whose address template contains *device_name*.

        Matches channels where ``{deviceName}`` appears in the address
        template and the channel's parameter enum (if present) includes
        *device_name*. Used by UC1 (capability enforcement) and UC2
        (mandatory topic validation) to find a device's expected topic
        surface.
        """
        result: list[ChannelSchema] = []
        for ch in self.channels.values():
            if "{deviceName}" in ch.address_template:
                result.append(ch)
            elif device_name in ch.address:
                # Concrete channel with device name baked in
                result.append(ch)
        return result

    def required_channels_for_tag(self, tag: str) -> list[ChannelSchema]:
        """Return channels that require *tag* via ``x-cosalette-requires``.

        A channel appears in the result if any of its
        :attr:`ChannelSchema.capability_requirements` has a matching
        ``tag`` value. Used by UC1 to enforce that tagged devices have
        all required registrations.
        """
        return [
            ch
            for ch in self.channels.values()
            if any(req.tag == tag for req in ch.capability_requirements)
        ]

    def payload_schema_for_topic(
        self, resolved_topic: str,
    ) -> dict[str, Any] | None:
        """Look up the JSON Schema for a fully-resolved MQTT topic.

        Iterates channels and matches *resolved_topic* against each
        channel's concrete address or address template (with parameter
        segments treated as wildcards). Returns ``None`` when no
        channel matches or when the matching channel has no payload
        schema. Used by :class:`PayloadValidator` (UC3) at publish time.
        """
        for ch in self.channels.values():
            if _topic_matches(ch.address_template, resolved_topic):
                return ch.payload_schema
        return None
```

The module-level helper used by `payload_schema_for_topic`:

```python
import re


def _topic_matches(template: str, topic: str) -> bool:
    """Check whether *topic* matches an address template.

    Parameter placeholders (``{...}``) are converted to ``[^/]+`` regex
    segments. A concrete address like ``vito2mqtt/status`` matches
    itself exactly.

    >>> _topic_matches("{appName}/{deviceName}/state", "vito2mqtt/temperature/state")
    True
    >>> _topic_matches("vito2mqtt/status", "vito2mqtt/status")
    True
    >>> _topic_matches("{appName}/{deviceName}/state", "vito2mqtt/status")
    False
    """
    pattern = re.sub(r"\{[^}]+\}", "[^/]+", template)
    return re.fullmatch(pattern, topic) is not None
```

#### x-cosalette-* Extension Schemas

Each `x-cosalette-*` extension property has a JSON Schema that validates the extension
itself. These schemas are used by the schema loader to reject malformed extensions
early and by IDE tooling (UC6) to provide authoring assistance.

##### `x-cosalette-enforcement`

Document-level enforcement configuration. Placed at the AsyncAPI root alongside
`asyncapi`, `info`, and `channels`.

```yaml
# x-cosalette-enforcement.schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
title: x-cosalette-enforcement
description: >
  Document-level enforcement settings. Controls validation mode and
  the lifecycle hooks at which enforcement runs.
type: object
required: [mode]
properties:
  mode:
    type: string
    enum: [strict, warn, "off"]
    description: >
      "strict" = fatal on violation, "warn" = log and continue,
      "off" = skip all validation.
  hooks:
    type: object
    properties:
      on_configure:
        type: boolean
        default: true
        description: >
          Validate registrations during the on_configure lifecycle
          phase (ADR-023). Covers UC1 (capability enforcement) and
          UC2 (mandatory topics).
      on_publish:
        type: boolean
        default: false
        description: >
          Validate payloads before each MQTT publish (UC3). Adds
          per-message overhead — intended for development/CI.
    additionalProperties: false
additionalProperties: false
```

##### `x-cosalette-requires`

Channel-level capability requirement. Placed on a channel to declare that devices
carrying the given tag must cover this channel.

```yaml
# x-cosalette-requires.schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
title: x-cosalette-requires
description: >
  Tag-based capability requirement. Declares that devices whose tag set
  includes the specified tag must provide a registration matching this
  channel. Supports UC1 (device capability enforcement).
type: object
required: [tag]
properties:
  tag:
    type: string
    minLength: 1
    description: >
      The device tag that triggers this requirement (ADR-010).
  description:
    type: string
    description: >
      Human-readable explanation of why this capability is required.
additionalProperties: false
```

##### `x-cosalette-archetype`

Operation-level archetype annotation. Maps an AsyncAPI operation to a cosalette
registration decorator.

```yaml
# x-cosalette-archetype.schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
title: x-cosalette-archetype
description: >
  Maps an AsyncAPI operation to a cosalette archetype (ADR-010).
  "telemetry" → @app.telemetry (send), "command" → @app.command
  (receive), "device" → @app.device (send + receive).
type: string
enum: [telemetry, command, device]
```

##### `x-cosalette-coalescing-group`

Operation-level coalescing group membership. References a coalescing group name as
defined in ADR-018.

```yaml
# x-cosalette-coalescing-group.schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
title: x-cosalette-coalescing-group
description: >
  Declares that this operation's telemetry publishes belong to the
  named coalescing group (ADR-018). All operations in the same group
  are published atomically in a single MQTT batch.
type: string
minLength: 1
```

#### PayloadValidator

The `PayloadValidator` pre-compiles `jsonschema.Draft7Validator` instances at
construction time and reuses them on every `validate()` call. This avoids the cost of
re-parsing JSON Schema on each publish — critical for the `on_publish` enforcement
hook (UC3) where validation sits in the hot path.

```python
from dataclasses import dataclass, field

import jsonschema


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single payload validation problem.

    Returned by :meth:`PayloadValidator.validate` to report schema
    mismatches without raising exceptions — the caller (enforcement
    hook) decides severity based on :attr:`EnforcementConfig.mode`.
    """

    channel_name: str
    topic: str
    message: str
    schema_path: str  # JSON Pointer into the schema (e.g. "/properties/unit")
    severity: Literal["error", "warning"] = "error"


class PayloadValidator:
    """Pre-compiled payload validators for all channels with schemas.

    Constructed once per :class:`SchemaRegistry` and reused for the
    lifetime of the registry. When the schema is hot-reloaded (UC4),
    a new ``PayloadValidator`` is built from the new registry.

    Thread safety: ``jsonschema.Draft7Validator`` instances are
    immutable after construction. The ``_validators`` dict is built
    in ``__init__`` and never mutated — safe to call ``validate()``
    from multiple async tasks concurrently.
    """

    def __init__(self, registry: SchemaRegistry) -> None:
        self._registry = registry
        self._validators: dict[str, tuple[str, jsonschema.Draft7Validator]] = {}
        self._build_validators()

    def _build_validators(self) -> None:
        """Pre-compile a Draft7Validator for each channel with a payload schema."""
        for channel_name, ch in self._registry.channels.items():
            if ch.payload_schema is not None:
                validator = jsonschema.Draft7Validator(
                    ch.payload_schema,
                    format_checker=jsonschema.FormatChecker(),
                )
                # Validate the schema itself at construction time — fail
                # fast if the schema document contains invalid JSON Schema.
                jsonschema.Draft7Validator.check_schema(ch.payload_schema)
                self._validators[ch.address_template] = (channel_name, validator)

    def validate(
        self, topic: str, payload: dict[str, Any],
    ) -> list[ValidationIssue]:
        """Validate *payload* against the schema for *topic*.

        Returns an empty list when the payload is valid or when no
        schema is registered for *topic*. Never raises — the caller
        is responsible for interpreting issues according to the
        enforcement mode.
        """
        issues: list[ValidationIssue] = []

        for template, (channel_name, validator) in self._validators.items():
            if not _topic_matches(template, topic):
                continue

            for error in validator.iter_errors(payload):
                issues.append(
                    ValidationIssue(
                        channel_name=channel_name,
                        topic=topic,
                        message=error.message,
                        schema_path="/".join(str(p) for p in error.absolute_schema_path),
                    ),
                )
            break  # first matching template wins

        return issues
```

Key design decisions:

- **`Draft7Validator`** rather than `Draft202012Validator`. AsyncAPI 3.0.0 message
  payloads use JSON Schema Draft 07 (the AsyncAPI specification inherits this from
  earlier versions). Using a newer draft would risk validation mismatches on features
  like `$dynamicRef` that AsyncAPI does not support.
- **`FormatChecker` enabled.** Formats like `date-time` on error timestamps (see the
  `ErrorPayload` in Section 2.1) are validated rather than silently accepted. This
  catches mistakes like `"2026-13-45"` during development.
- **Schema self-check at construction.** `check_schema()` ensures the AsyncAPI
  document's payload schemas are themselves valid JSON Schema. A malformed schema
  produces a clear error at app startup rather than mysterious validation behaviour
  at publish time.
- **First-match semantics.** When multiple channel templates could match a topic
  (unlikely given ADR-002's flat topic structure and ADR-019's scoped name uniqueness),
  the first match wins. This is deterministic because `channels` is a `dict` preserving
  insertion order from the AsyncAPI document.

#### Worked Example: vito2mqtt SchemaRegistry

The following shows a `SchemaRegistry` instance as it would be constructed by the
schema loader from the vito2mqtt AsyncAPI document in Section 2.1. This covers the
core device archetypes — temperature (telemetry), valve (bidirectional device) — plus
the app-level status, error, and availability channels.

```python
registry = SchemaRegistry(
    app_name="vito2mqtt",
    app_version="0.2.0",
    asyncapi_version="3.0.0",
    enforcement=EnforcementConfig(
        mode="strict",
        on_configure=True,
        on_publish=True,
    ),
    channels={
        # --- Telemetry: temperature (send only) -----------------------
        "temperatureState": ChannelSchema(
            address="vito2mqtt/temperature/state",
            address_template="{appName}/{deviceName}/state",
            direction="send",
            payload_schema={
                "type": "object",
                "required": ["temperature", "unit"],
                "properties": {
                    "temperature": {"type": "number"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "additionalProperties": False,
            },
            mqtt_binding=MqttBinding(qos=1, retain=True),
            archetype="telemetry",
            message_name="temperatureReading",
        ),
        # --- Device: valve (send + receive) ---------------------------
        "valveState": ChannelSchema(
            address="vito2mqtt/valve/state",
            address_template="{appName}/{deviceName}/state",
            direction="send",
            payload_schema={
                "type": "object",
                "required": ["position"],
                "properties": {
                    "position": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "additionalProperties": False,
            },
            mqtt_binding=MqttBinding(qos=1, retain=True),
            archetype="device",
            message_name="valveReading",
        ),
        "valveSet": ChannelSchema(
            address="vito2mqtt/valve/set",
            address_template="{appName}/{deviceName}/set",
            direction="receive",
            payload_schema={
                "type": "object",
                "required": ["position"],
                "properties": {
                    "position": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
            },
            mqtt_binding=MqttBinding(qos=1, retain=False),
            archetype="device",
            message_name="valveCommand",
        ),
        # --- App-level: status ----------------------------------------
        "appStatus": ChannelSchema(
            address="vito2mqtt/status",
            address_template="vito2mqtt/status",
            direction="send",
            payload_schema={
                "type": "object",
                "required": ["online"],
                "properties": {"online": {"type": "boolean"}},
            },
            mqtt_binding=MqttBinding(qos=1, retain=True),
            message_name="statusMessage",
        ),
        # --- App-level: error -----------------------------------------
        "appError": ChannelSchema(
            address="vito2mqtt/error",
            address_template="vito2mqtt/error",
            direction="send",
            payload_schema={
                "type": "object",
                "required": ["error", "timestamp"],
                "properties": {
                    "error": {"type": "string"},
                    "timestamp": {"type": "string", "format": "date-time"},
                    "device": {"type": "string"},
                },
            },
            mqtt_binding=MqttBinding(qos=0, retain=False),
            message_name="errorMessage",
        ),
        # --- Per-device: availability ---------------------------------
        "deviceAvailability": ChannelSchema(
            address="vito2mqtt/{deviceName}/availability",
            address_template="{appName}/{deviceName}/availability",
            direction="send",
            payload_schema={
                "type": "object",
                "required": ["online"],
                "properties": {"online": {"type": "boolean"}},
            },
            mqtt_binding=MqttBinding(qos=1, retain=True),
            message_name="availabilityMessage",
        ),
    },
    operations={
        "publishTemperature": OperationSchema(
            action="send",
            channel_ref="temperatureState",
            archetype="telemetry",
            mqtt_binding=MqttBinding(qos=1, retain=True),
        ),
        "publishValveState": OperationSchema(
            action="send",
            channel_ref="valveState",
            archetype="device",
            mqtt_binding=MqttBinding(qos=1, retain=True),
        ),
        "receiveValveCommand": OperationSchema(
            action="receive",
            channel_ref="valveSet",
            archetype="device",
            mqtt_binding=MqttBinding(qos=1, retain=False),
        ),
        "publishStatus": OperationSchema(
            action="send",
            channel_ref="appStatus",
            mqtt_binding=MqttBinding(qos=1, retain=True),
        ),
        "publishError": OperationSchema(
            action="send",
            channel_ref="appError",
            mqtt_binding=MqttBinding(qos=0, retain=False),
        ),
        "publishAvailability": OperationSchema(
            action="send",
            channel_ref="deviceAvailability",
            mqtt_binding=MqttBinding(qos=1, retain=True),
        ),
    },
    component_schemas={
        "TemperaturePayload": {
            "type": "object",
            "required": ["temperature", "unit"],
            "properties": {
                "temperature": {"type": "number"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "additionalProperties": False,
        },
        "ValvePayload": {
            "type": "object",
            "required": ["position"],
            "properties": {
                "position": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "additionalProperties": False,
        },
        "ValveCommandPayload": {
            "type": "object",
            "required": ["position"],
            "properties": {
                "position": {"type": "integer", "minimum": 0, "maximum": 100},
            },
        },
        "StatusPayload": {
            "type": "object",
            "required": ["online"],
            "properties": {"online": {"type": "boolean"}},
        },
        "ErrorPayload": {
            "type": "object",
            "required": ["error", "timestamp"],
            "properties": {
                "error": {"type": "string"},
                "timestamp": {"type": "string", "format": "date-time"},
                "device": {"type": "string"},
            },
        },
        "AvailabilityPayload": {
            "type": "object",
            "required": ["online"],
            "properties": {"online": {"type": "boolean"}},
        },
    },
    device_names=frozenset({"temperature", "valve"}),
)
```

With this registry in hand, the enforcement hooks can answer questions directly:

```python
# UC1 — capability enforcement during on_configure
battery_channels = registry.required_channels_for_tag("battery_powered")
# → [] (no channels require battery_powered in vito2mqtt)

# UC2 — which channels must temperature satisfy?
temp_channels = registry.channels_for_device("temperature")
# → [ChannelSchema(address_template="{appName}/{deviceName}/state", ...),
#    ChannelSchema(address_template="{appName}/{deviceName}/availability", ...)]

# UC3 — payload validation at publish time
schema = registry.payload_schema_for_topic("vito2mqtt/temperature/state")
# → {"type": "object", "required": ["temperature", "unit"], ...}

validator = PayloadValidator(registry)
issues = validator.validate(
    "vito2mqtt/temperature/state",
    {"temp": 22.5},  # wrong key — "temperature" is required
)
# → [ValidationIssue(channel_name="temperatureState",
#                     topic="vito2mqtt/temperature/state",
#                     message="'temperature' is a required property",
#                     schema_path="required")]
```

### 4.2 Schema Loader Design (COS-5hx.8)

#### Module Overview

`cosalette/_schema_loader.py` is the I/O and parsing counterpart to the pure data-model
module `_schema.py` (§4.1). Its single responsibility: **load raw AsyncAPI YAML from any
source, resolve `$ref` pointers, validate `x-cosalette-*` extensions, and return a
fully populated `SchemaRegistry`**.

Dependencies — both already in the project's dependency tree:

| Package        | Role                                        |
| -------------- | ------------------------------------------- |
| `pyyaml`       | YAML parsing (`yaml.safe_load`)             |
| `jsonschema`   | Extension validation, payload schema checks |

Design principles carry over from §4.1: the loader produces **immutable, frozen
dataclasses** and never mutates them after construction. All I/O is async-safe — sync
filesystem reads are wrapped in `asyncio.to_thread` so the event loop is never blocked.

#### SchemaSource Protocol

Schema documents can originate from different locations (UC4 — distribution). The loader
accepts any object satisfying the `SchemaSource` protocol:

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml


@runtime_checkable
class SchemaSource(Protocol):
    """Provides raw AsyncAPI YAML from an arbitrary location."""

    async def load(self) -> str:
        """Return the complete YAML document as a string."""
        ...

    @property
    def description(self) -> str:
        """Human-readable origin (used in error messages)."""
        ...
```

Four concrete implementations cover the known distribution channels and testing:

```python
@dataclass(frozen=True)
class FileSchemaSource:
    """Load from a local filesystem path (UC4 — primary)."""

    path: Path

    async def load(self) -> str:
        return await asyncio.to_thread(self.path.read_text, encoding="utf-8")

    @property
    def description(self) -> str:
        return f"file://{self.path}"
```

```python
@dataclass(frozen=True)
class MqttSchemaSource:
    """Load from a retained MQTT message (UC4 — broker distribution)."""

    topic: str
    mqtt: MqttPort  # cosalette's outbound MQTT port

    async def load(self) -> str:
        msg = await self.mqtt.get_retained(self.topic)
        if msg is None:
            raise SchemaLoadError(
                errors=[f"No retained message on topic '{self.topic}'"],
                source_description=self.description,
            )
        return msg.payload.decode("utf-8")

    @property
    def description(self) -> str:
        return f"mqtt://{self.topic}"
```

```python
@dataclass(frozen=True)
class HttpSchemaSource:
    """Load via HTTP GET (UC4 — fleet management endpoint)."""

    url: str

    async def load(self) -> str:
        import httpx  # optional dependency, imported lazily

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(self.url)
            resp.raise_for_status()
            return resp.text

    @property
    def description(self) -> str:
        return self.url
```

```python
@dataclass(frozen=True)
class InlineSchemaSource:
    """Pass raw YAML directly — used exclusively in tests."""

    content: str

    async def load(self) -> str:
        return self.content

    @property
    def description(self) -> str:
        return "<inline>"
```

#### $ref Resolution

AsyncAPI documents lean heavily on `$ref` for DRY definitions. The loader resolves
**all** `$ref` entries before extraction begins, producing a fully inlined document.

```python
def _resolve_refs(
    doc: dict,
    *,
    base_path: Path | None = None,
    _visited: frozenset[str] = frozenset(),
) -> dict:
    """Recursively resolve ``$ref`` pointers in *doc*.

    Handles two flavours:

    * **Internal** — ``#/components/schemas/Foo`` (JSON Pointer into *doc*).
    * **External** — ``./schemas/foo.yaml#/Definitions/Bar`` (file-relative,
      resolved against *base_path*).

    Parameters
    ----------
    doc:
        The top-level parsed YAML dict (or a sub-tree during recursion).
    base_path:
        Directory used to resolve external file refs.  ``None`` disables
        external resolution (raises on encounter).
    _visited:
        Accumulated ref strings for **circular-reference detection**.
        If a ref is seen twice, :class:`SchemaLoadError` is raised.

    Returns
    -------
    dict
        A **new** dict with every ``$ref`` replaced by the target content.
        The original *doc* is never mutated.
    """
    if not isinstance(doc, dict):
        return doc

    if "$ref" in doc:
        ref: str = doc["$ref"]
        if ref in _visited:
            raise SchemaLoadError(
                errors=[f"Circular $ref detected: {ref}"],
                source_description="$ref resolution",
            )
        visited = _visited | {ref}

        if ref.startswith("#/"):
            # Internal JSON Pointer
            resolved = _follow_pointer(doc, ref)
        else:
            # External file ref — e.g. "./schemas/foo.yaml#/Definitions/Bar"
            if base_path is None:
                raise SchemaLoadError(
                    errors=[f"External $ref '{ref}' but no base_path provided"],
                    source_description="$ref resolution",
                )
            resolved = _load_external_ref(ref, base_path)

        # Recurse into the resolved subtree (may contain further $refs)
        return _resolve_refs(resolved, base_path=base_path, _visited=visited)

    # No $ref at this level — recurse into children
    return {
        k: (
            _resolve_refs(v, base_path=base_path, _visited=_visited)
            if isinstance(v, dict)
            else (
                [_resolve_refs(item, base_path=base_path, _visited=_visited)
                 for item in v]
                if isinstance(v, list)
                else v
            )
        )
        for k, v in doc.items()
    }
```

Helper for internal pointers:

```python
def _follow_pointer(root: dict, pointer: str) -> dict:
    """Navigate a JSON Pointer like ``#/components/schemas/Foo``."""
    parts = pointer.lstrip("#/").split("/")
    node = root
    for part in parts:
        try:
            node = node[part]
        except (KeyError, TypeError) as exc:
            raise SchemaLoadError(
                errors=[f"Unresolvable $ref pointer: {pointer} (failed at '{part}')"],
                source_description="$ref resolution",
            ) from exc
    return node
```

> **Note:** If external-ref complexity grows (nested external files referencing other
> external files), the `jsonschema.referencing` library provides a battle-tested registry
> approach. For the initial implementation, the hand-rolled walker above is sufficient
> and avoids adding configuration surface area.

#### Channel Extraction

`_extract_channels` iterates the `channels` section of the resolved document and builds
one `ChannelSchema` (§4.1) per entry:

```python
def _extract_channels(doc: dict) -> dict[str, ChannelSchema]:
    """Convert AsyncAPI ``channels`` into :class:`ChannelSchema` instances.

    For each channel:

    1. Read ``address`` → ``address_template``.
    2. Collect ``parameters`` (name → JSON Schema or enum list).
    3. Resolve the first message's ``payload`` to get the JSON Schema dict.
    4. Build :class:`MqttBinding` from ``bindings.mqtt`` (QoS, retain).
    5. Build :class:`CapabilityRequirement` list from ``x-cosalette-requires``.
    6. Read ``x-cosalette-archetype`` if present.
    7. Determine ``direction`` by scanning operations that reference this
       channel (``send``, ``receive``, or ``both``).
    """
    channels: dict[str, ChannelSchema] = {}

    for name, ch_obj in doc.get("channels", {}).items():
        address = ch_obj.get("address", "")
        parameters = ch_obj.get("parameters", {})

        # Payload — first message's payload schema (already $ref-resolved)
        messages = ch_obj.get("messages", {})
        first_msg = next(iter(messages.values()), {})
        payload_schema = first_msg.get("payload", {})

        # MQTT bindings (channel-level defaults)
        mqtt_raw = ch_obj.get("bindings", {}).get("mqtt", {})
        mqtt_binding = MqttBinding(
            qos=mqtt_raw.get("qos", 0),
            retain=mqtt_raw.get("retain", False),
        )

        # x-cosalette-requires → CapabilityRequirement list
        cap_reqs = [
            CapabilityRequirement(
                capability=req["capability"],
                reason=req.get("reason", ""),
            )
            for req in ch_obj.get("x-cosalette-requires", [])
        ]

        archetype = ch_obj.get("x-cosalette-archetype")

        channels[name] = ChannelSchema(
            name=name,
            address_template=address,
            parameters=parameters,
            payload_schema=payload_schema,
            mqtt_binding=mqtt_binding,
            capability_requirements=cap_reqs,
            archetype=archetype,
        )

    return channels
```

#### Operation Extraction

`_extract_operations` maps AsyncAPI 3.0.0 operations to `OperationSchema` (§4.1),
linking each operation to its parent channel and merging operation-level MQTT bindings
over channel defaults:

```python
def _extract_operations(
    doc: dict,
    channels: dict[str, ChannelSchema],
) -> dict[str, OperationSchema]:
    """Convert AsyncAPI ``operations`` into :class:`OperationSchema` instances.

    Operation-level ``bindings.mqtt`` values **override** the channel-level
    defaults established by :func:`_extract_channels`.  The ``x-cosalette-archetype``
    and ``x-cosalette-coalescing-group`` extensions are read here.
    """
    operations: dict[str, OperationSchema] = {}

    for name, op_obj in doc.get("operations", {}).items():
        action = op_obj.get("action", "send")  # "send" | "receive"

        # Channel linkage — AsyncAPI 3 uses channel.$ref
        channel_ref_obj = op_obj.get("channel", {})
        channel_key = channel_ref_obj.get("$ref", "").rsplit("/", 1)[-1]

        # Operation-level MQTT binding overrides
        mqtt_raw = op_obj.get("bindings", {}).get("mqtt", {})
        if mqtt_raw:
            mqtt_binding = MqttBinding(
                qos=mqtt_raw.get("qos", 0),
                retain=mqtt_raw.get("retain", False),
            )
        elif channel_key in channels:
            mqtt_binding = channels[channel_key].mqtt_binding
        else:
            mqtt_binding = MqttBinding()

        operations[name] = OperationSchema(
            name=name,
            action=action,
            channel_ref=channel_key,
            archetype=op_obj.get("x-cosalette-archetype"),
            coalescing_group=op_obj.get("x-cosalette-coalescing-group"),
            mqtt_binding=mqtt_binding,
        )

    return operations
```

#### Extension Validation

Before building the registry, all `x-cosalette-*` extensions are validated against the
JSON Schemas defined in §4.1. This catches malformed extensions **early** — before they
propagate into dataclass construction where the error would be harder to diagnose.

```python
def _validate_extensions(doc: dict) -> list[str]:
    """Validate ``x-cosalette-*`` properties throughout the document.

    Returns a list of human-readable error strings (empty → valid).
    Checks:

    - Document-level ``x-cosalette-enforcement`` matches the
      :class:`EnforcementConfig` schema (mode, hooks).
    - Channel-level ``x-cosalette-requires`` entries each have a non-empty
      ``capability`` string.
    - Channel/operation-level ``x-cosalette-archetype`` is a known archetype
      name from the device archetype registry (ADR-010).
    - Operation-level ``x-cosalette-coalescing-group`` is a non-empty string
      when present (ADR-018).
    """
    errors: list[str] = []

    # Document-level enforcement
    enforcement = doc.get("x-cosalette-enforcement")
    if enforcement is not None:
        if not isinstance(enforcement, dict):
            errors.append("x-cosalette-enforcement must be a mapping")
        else:
            mode = enforcement.get("mode")
            if mode not in {"strict", "warn", "off", None}:
                errors.append(
                    f"x-cosalette-enforcement.mode: unknown value '{mode}'"
                )

    # Channel-level extensions
    for ch_name, ch_obj in doc.get("channels", {}).items():
        for i, req in enumerate(ch_obj.get("x-cosalette-requires", [])):
            if not req.get("capability"):
                errors.append(
                    f"channels.{ch_name}.x-cosalette-requires[{i}]: "
                    "'capability' is required and must be non-empty"
                )
        archetype = ch_obj.get("x-cosalette-archetype")
        if archetype is not None and not isinstance(archetype, str):
            errors.append(
                f"channels.{ch_name}.x-cosalette-archetype: must be a string"
            )

    # Operation-level extensions
    for op_name, op_obj in doc.get("operations", {}).items():
        cg = op_obj.get("x-cosalette-coalescing-group")
        if cg is not None and (not isinstance(cg, str) or not cg.strip()):
            errors.append(
                f"operations.{op_name}.x-cosalette-coalescing-group: "
                "must be a non-empty string"
            )

    return errors
```

#### Top-Level Load Function

The public entry-point orchestrates the full pipeline — load → parse → resolve →
validate → extract → build:

```python
async def load_schema(
    source: SchemaSource,
    *,
    base_path: Path | None = None,
) -> SchemaRegistry:
    """Load an AsyncAPI 3.0.0 document and return a :class:`SchemaRegistry`.

    Pipeline:

    1. Fetch raw YAML via *source*.
    2. Parse YAML with ``yaml.safe_load``.
    3. Assert ``asyncapi`` version is ``3.0.0``.
    4. Resolve all ``$ref`` pointers (internal and external).
    5. Validate ``x-cosalette-*`` extensions.
    6. Extract :class:`EnforcementConfig` (or apply defaults).
    7. Extract channels → ``dict[str, ChannelSchema]``.
    8. Extract operations → ``dict[str, OperationSchema]``.
    9. Collect component schemas for later payload validation.
    10. Derive device names from channel parameter enums.
    11. Construct and return :class:`SchemaRegistry`.

    Parameters
    ----------
    source:
        Any :class:`SchemaSource` implementation.
    base_path:
        Root directory for resolving external ``$ref`` paths.  Defaults to
        ``None`` (external refs disabled).  :class:`FileSchemaSource` sets
        this automatically to the schema file's parent directory.

    Raises
    ------
    SchemaLoadError
        On YAML parse failure, version mismatch, unresolvable refs,
        circular refs, or invalid extensions.
    """
    raw = await source.load()

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SchemaLoadError(
            errors=[f"YAML parse error: {exc}"],
            source_description=source.description,
        ) from exc

    if not isinstance(doc, dict):
        raise SchemaLoadError(
            errors=["Document root must be a YAML mapping"],
            source_description=source.description,
        )

    # Version gate
    version = doc.get("asyncapi", "")
    if version != "3.0.0":
        raise SchemaLoadError(
            errors=[f"Unsupported asyncapi version '{version}' (expected '3.0.0')"],
            source_description=source.description,
        )

    # Infer base_path for FileSchemaSource
    if base_path is None and isinstance(source, FileSchemaSource):
        base_path = source.path.parent

    doc = _resolve_refs(doc, base_path=base_path)

    # Extension validation
    ext_errors = _validate_extensions(doc)
    if ext_errors:
        raise SchemaLoadError(errors=ext_errors, source_description=source.description)

    # Enforcement config (document-level)
    enforcement = _build_enforcement_config(
        doc.get("x-cosalette-enforcement", {})
    )

    channels = _extract_channels(doc)
    operations = _extract_operations(doc, channels)

    # Component schemas  (for PayloadValidator reuse)
    component_schemas = doc.get("components", {}).get("schemas", {})

    # Device names — union of all parameter enums named "deviceName"
    device_names: set[str] = set()
    for ch in channels.values():
        dn_param = ch.parameters.get("deviceName", {})
        device_names.update(dn_param.get("enum", []))

    return SchemaRegistry(
        asyncapi_version=version,
        enforcement=enforcement,
        channels=channels,
        operations=operations,
        component_schemas=component_schemas,
        device_names=sorted(device_names),
    )
```

#### Error Handling

All loader failures surface through a single exception type that accumulates multiple
errors (a schema document can have several independent problems):

```python
@dataclass
class SchemaLoadError(Exception):
    """Raised when an AsyncAPI document cannot be loaded or is invalid.

    Attributes
    ----------
    errors:
        One or more human-readable error descriptions.
    source_description:
        Where the schema was loaded from (e.g. ``file:///path/to/schema.yaml``
        or ``mqtt://myapp/schema/document``).
    """

    errors: list[str]
    source_description: str

    def __str__(self) -> str:
        header = f"Failed to load schema from {self.source_description}"
        if len(self.errors) == 1:
            return f"{header}: {self.errors[0]}"
        bullet_list = "\n".join(f"  - {e}" for e in self.errors)
        return f"{header} ({len(self.errors)} errors):\n{bullet_list}"
```

Error categories handled by the pipeline:

| Category                     | Raised by              | Example                                          |
| ---------------------------- | ---------------------- | ------------------------------------------------ |
| YAML parse error             | `yaml.safe_load`       | Malformed YAML (tabs, unclosed quotes)            |
| AsyncAPI version mismatch    | `load_schema`          | `asyncapi: 2.6.0` instead of `3.0.0`             |
| Unresolvable `$ref`          | `_follow_pointer`      | `#/components/schemas/Nonexistent`                |
| Circular `$ref`              | `_resolve_refs`        | A → B → A                                         |
| Invalid `x-cosalette-*`     | `_validate_extensions` | `x-cosalette-enforcement.mode: "yolo"`            |
| Missing required fields      | `_extract_channels`    | Document has no `channels` section                |
| External ref without base    | `_resolve_refs`        | `./foo.yaml` ref but `base_path` is `None`        |

#### Caching Strategy

`SchemaRegistry` is built from frozen dataclasses (§4.1) and is therefore **safe to
cache and share** across async tasks without synchronization.

| Aspect              | Strategy                                                         |
| ------------------- | ---------------------------------------------------------------- |
| **Cache key**       | Source path/URL + file `mtime` (filesystem) or message timestamp (MQTT) |
| **Invalidation**    | Explicit only — `cosalette schema reload` CLI command or MQTT control message on `{app}/schema/reload` |
| **Automatic poll**  | None. Schema is loaded **once at startup** and held for the lifetime of the process. Polling would add complexity and risk mid-flight schema changes |
| **Hot reload**      | Atomic reference swap: build a new `SchemaRegistry`, then replace the single reference in `SchemaAdapter`. Old registry is GC'd once no task holds a reference |
| **Test behavior**   | Always use `InlineSchemaSource` — no caching, no filesystem. Each test constructs its own `SchemaRegistry` from literal YAML |

This matches UC5 (migration) expectations: a v1 → v2 schema migration is handled by
loading the new document and swapping the registry reference, with no in-place mutation
of the existing data model.

### 4.3 Lifecycle Integration (COS-5hx.7)

This section defines exactly where schema enforcement plugs into cosalette's startup
lifecycle (`App._run_async()`), what settings control it, and how violations surface to
the developer. The design is **purely additive** — no existing public API changes, and
schema enforcement remains fully opt-in.

#### Insertion Point

The framework bootstrap in `_run_async()` executes these steps in order:

1. `resolve_settings()` — merge config sources into a `Settings` instance
2. `configure_logging()` — set up structured logging
3. `resolve_adapters()` — instantiate and resolve adapter instances
4. `run_configure_hooks()` — execute user `@app.on_configure` hooks (ADR-023)
5. `expand_name_specs()` — finalize all device names, including dynamic `name_spec` callables
6. `resolve_intervals()` — resolve deferred interval expressions (ADR-020)
7. **`load_and_validate_schema()`** — **NEW: load schema, validate registrations**
8. `create_mqtt()` — connect to the MQTT broker
9. _(remaining wire and run phases unchanged)_

The insertion point between steps 6 and 8 is deliberate:

- **After `expand_name_specs()` (step 5):** Device names must be finalized before we can
  check whether a schema-required channel has a matching registration. Name-spec
  callables may produce dynamic device lists from settings — until they execute, the
  device set is incomplete.
- **After `resolve_intervals()` (step 6):** Interval data is needed for coalescing-group
  alignment checks (ADR-018).
- **After `run_configure_hooks()` (step 4):** User hooks may modify settings or adapter
  state. Schema validation must see the final configuration.
- **Before `create_mqtt()` (step 8):** Strict-mode violations must raise _before_ the
  app connects to the broker. Fail-fast prevents a misconfigured app from publishing
  invalid data.

The code change in `_run_async()`:

```python
# After expand_name_specs and resolve_intervals:
_wiring.expand_name_specs(self._telemetry, self._devices, self._commands, resolved_settings)
_wiring.resolve_intervals(self._telemetry, resolved_settings)

# NEW: Schema loading and validation
schema_registry = await _wiring.load_and_validate_schema(
    self, resolved_settings, prefix
)

mqtt_client = _wiring.create_mqtt(mqtt, resolved_settings, prefix, self._name)
```

If `load_and_validate_schema()` returns `None` (no schema configured), the rest of the
lifecycle proceeds exactly as before.

#### Settings Extension

Schema settings follow the existing pydantic-settings pattern (ADR-003). A new
`SchemaSettings` nested model is added to `Settings`:

```python
from pydantic import BaseModel


class SchemaSettings(BaseModel):
    """Schema enforcement configuration."""

    path: str | None = None  # path to AsyncAPI YAML; defaults to "schema/asyncapi.yaml"
    enforcement: Literal["strict", "warn", "off"] = "warn"
    on_publish: bool = False  # enable payload validation on each publish call
```

Integrated into the root `Settings` class:

```python
class Settings(BaseSettings):
    mqtt: MqttSettings = MqttSettings()
    logging: LoggingSettings = LoggingSettings()
    schema_: SchemaSettings = Field(default_factory=SchemaSettings, alias="schema")
```

Accessed as `settings.schema_.path`, `settings.schema_.enforcement`, and
`settings.schema_.on_publish`. The trailing underscore avoids shadowing Python's
built-in `schema` in contexts where that matters; the `alias="schema"` ensures YAML
and env-var keys remain clean.

**Environment variable mapping** follows the double-underscore nesting convention:

| Setting                 | Env Var                             | Example              |
| ----------------------- | ----------------------------------- | -------------------- |
| `schema.path`           | `COSALETTE_SCHEMA__PATH`            | `./my-schema.yaml`   |
| `schema.enforcement`    | `COSALETTE_SCHEMA__ENFORCEMENT`     | `strict`             |
| `schema.on_publish`     | `COSALETTE_SCHEMA__ON_PUBLISH`      | `true`               |

All three fields have defaults, so zero configuration is needed to keep existing apps
running unchanged.

#### The `load_and_validate_schema` Wiring Function

A new async function in `_wiring.py`:

```python
async def load_and_validate_schema(
    app: App,
    settings: Settings,
    prefix: str,
) -> SchemaRegistry | None:
    """Load the schema document and validate app registrations against it.

    Returns the SchemaRegistry if a schema was loaded, or None if schema
    enforcement is disabled or no schema file is found.
    """
```

Execution steps:

1. **Check enforcement mode.** If `settings.schema_.enforcement == "off"`, log at DEBUG
   level and return `None` immediately.
2. **Resolve schema path.** If `settings.schema_.path` is `None`, check for the
   conventional default `schema/asyncapi.yaml` relative to the working directory. If
   neither is set and the default file does not exist, log at INFO ("No schema document
   found — schema enforcement is inactive") and return `None`.
3. **Create source.** Instantiate `FileSchemaSource(path)` from §4.2.
4. **Load schema.** Call `await load_schema(source)`. On `SchemaLoadError`, behaviour
   depends on enforcement mode:
   - _strict_: re-raise — the app cannot start with an unparsable schema.
   - _warn_: log the error at WARNING, return `None` — the app starts without schema
     enforcement.
5. **Override enforcement mode.** If `settings.schema_.enforcement` differs from the
   document-level `x-cosalette-enforcement` extension, the settings value wins. This
   allows operators to tighten or loosen enforcement without editing the schema file.
6. **Run registration validation.** Call `_validate_registrations(app, registry, prefix)`.
7. **Handle violations:**
   - _strict + violations_: raise `SchemaViolationError(violations)`.
   - _warn + violations_: log each violation at WARNING level.
   - _no violations_: log at INFO ("Schema validation passed").
8. **Store registry.** Assign `app._schema_registry = registry` for use by the
   on-publish validation path.
9. **Configure on-publish hook.** If `settings.schema_.on_publish` is `True` and the
   registry has `PayloadValidator` instances, attach a publish interceptor (see §4.4
   _On-Publish Validation_, to follow).
10. **Return** the `SchemaRegistry`.

#### Registration Validation

A private function in `_wiring.py`:

```python
def _validate_registrations(
    app: App,
    registry: SchemaRegistry,
    prefix: str,
) -> list[SchemaViolation]:
    """Compare schema-declared channels against the app's actual registrations.

    Returns a (possibly empty) list of violations.
    """
```

The function iterates over every `ChannelSchema` in the `SchemaRegistry` and performs
these checks:

1. **Mandatory channel presence (UC2).** For each channel that does _not_ carry an
   `x-cosalette-requires` extension: verify that at least one registration
   (`_TelemetryRegistration`, `_DeviceRegistration`, or `_CommandRegistration`) would
   publish or subscribe to a topic matching the channel's address pattern. A missing
   match produces a `"missing_channel"` violation.

2. **Capability enforcement (UC1).** For each channel carrying
   `x-cosalette-requires: <tag>`: look up all devices whose archetype or metadata
   includes the tag. For each such device, verify a matching registration exists.
   A missing match produces a `"missing_capability"` violation with the device name
   and required tag in the message.

3. **Archetype consistency.** If a channel's operation specifies
   `x-cosalette-archetype: telemetry`, verify the matching registration is a
   `_TelemetryRegistration` — not a bare `_DeviceRegistration`. Analogously for
   `command` → `_CommandRegistration`. A mismatch produces an `"archetype_mismatch"`
   violation.

4. **Device completeness.** If a channel's address contains a `{device}` parameter
   whose schema defines an `enum` of allowed device names, verify every listed name
   has a corresponding device registration. An unregistered name produces an
   `"unknown_device"` violation.

5. **Coalescing group alignment (ADR-018).** If an operation specifies
   `x-cosalette-coalescing-group: <group>`, verify the matching
   `_TelemetryRegistration` has `group=<group>`. A mismatch (or missing group)
   produces a `"group_mismatch"` violation.

The `SchemaViolation` data class captures each issue:

```python
@dataclass(frozen=True)
class SchemaViolation:
    """A single schema-vs-registration mismatch."""

    category: Literal[
        "missing_channel",
        "missing_capability",
        "archetype_mismatch",
        "unknown_device",
        "group_mismatch",
    ]
    channel_name: str | None
    device_name: str | None
    message: str
```

Each violation carries enough context for a clear, actionable log line:

```
WARNING  Schema violation [missing_capability] channel="home/{device}/battery"
         device="motion_sensor" — device tagged 'battery_powered' has no
         registration matching this channel
```

#### Enforcement Mode Behaviour

The enforcement mode controls how violations surface at two lifecycle points:

| Mode     | Registration violations (startup)                        | On-publish violations (runtime)                       |
| -------- | -------------------------------------------------------- | ----------------------------------------------------- |
| `strict` | Raise `SchemaViolationError` — app does not start        | Suppress publish, send structured error to `{app}/error` (ADR-011) |
| `warn`   | Log WARNING per violation, app starts normally           | Log WARNING, publish the message anyway               |
| `off`    | Skip validation entirely                                 | Skip validation entirely                              |

**Mode override precedence** (highest wins):

1. CLI flag: `--schema-enforcement strict`
2. Environment variable: `COSALETTE_SCHEMA__ENFORCEMENT=strict`
3. AsyncAPI document extension: `x-cosalette-enforcement: strict`
4. Default: `warn`

This precedence means operators can always override the document-embedded mode at
deploy time without modifying the schema file — useful for running strict in CI and
warn in production, or vice versa.

#### New Exception Types

Two exception classes, both inheriting from `CosError` (ADR-011):

```python
class SchemaViolationError(CosError):
    """Raised when schema violations are found in strict mode.

    Carries the full list of violations so callers (or error handlers) can
    inspect them programmatically.
    """

    def __init__(self, violations: list[SchemaViolation]) -> None:
        self.violations = violations
        summary = f"{len(violations)} schema violation(s) found"
        details = "; ".join(v.message for v in violations[:5])
        if len(violations) > 5:
            details += f"; ... and {len(violations) - 5} more"
        super().__init__(f"{summary}: {details}")
```

```python
class SchemaLoadError(CosError):
    """Raised when the schema document cannot be loaded or parsed.

    Defined in §4.2; listed here for completeness.
    """

    def __init__(self, errors: list[str], *, source_description: str) -> None:
        self.errors = errors
        self.source_description = source_description
        summary = f"Failed to load schema from {source_description}"
        super().__init__(f"{summary}: {'; '.join(errors[:3])}")
```

Both exceptions are importable from the public `cosalette` package namespace so that
user code can catch them in tests or custom error handlers.

#### Sequence Diagram

The full bootstrap lifecycle with schema validation inserted:

```
App._run_async()
│
├─ resolve_settings()
├─ configure_logging()
├─ resolve_adapters()
├─ run_configure_hooks()        ← user @on_configure hooks execute first
├─ expand_name_specs()          ← device names finalized (incl. dynamic name_specs)
├─ resolve_intervals()          ← intervals resolved (ADR-020)
│
├─ load_and_validate_schema()   ← NEW: schema enforcement gate
│   ├─ check enforcement mode
│   │   └─ [off] → return None (skip everything)
│   ├─ resolve schema path
│   │   └─ [not found] → log info, return None
│   ├─ FileSchemaSource(path)
│   ├─ load_schema(source) → SchemaRegistry
│   │   └─ [SchemaLoadError + strict] → raise (app cannot start)
│   │   └─ [SchemaLoadError + warn]   → log warning, return None
│   ├─ override enforcement from settings
│   ├─ _validate_registrations(app, registry, prefix)
│   │   ├─ check mandatory channel presence     (UC2)
│   │   ├─ check capability requirements         (UC1)
│   │   ├─ check archetype consistency
│   │   ├─ check device completeness
│   │   └─ check coalescing group alignment      (ADR-018)
│   ├─ [strict + violations] → raise SchemaViolationError
│   ├─ [warn + violations]   → log WARNING per violation
│   ├─ store registry on App instance
│   └─ return SchemaRegistry
│
├─ create_mqtt()                ← MQTT connection happens AFTER validation
├─ create_services()
├─ mqtt_client.start()
├─ install_signal_handlers()
│
├─ enter_lifecycle_adapters()
├─ publish_device_availability()
├─ publish_registry_snapshot()
├─ build_contexts()
├─ wire_router()
│
└─ run_lifespan_and_devices()
```

The key guarantee: **if strict mode is active and any violation exists, the app never
reaches `create_mqtt()`**. No broker connection is made, no messages are published.

#### Impact on Existing Code

The following files require changes:

| File               | Change                                                                                 |
| ------------------ | -------------------------------------------------------------------------------------- |
| `_app.py`          | Add `_schema_registry: SchemaRegistry \| None = None` attribute; insert `load_and_validate_schema()` call in `_run_async()` between `resolve_intervals()` and `create_mqtt()` |
| `_wiring.py`       | Add `load_and_validate_schema()` (async) and `_validate_registrations()` (sync) functions |
| `_settings.py`     | Add `SchemaSettings` model; add `schema_: SchemaSettings` field on `Settings` with alias `"schema"` |
| `_errors.py`       | Add `SchemaViolationError` (or create new `_schema_errors.py` alongside `SchemaLoadError` from §4.2) |
| `_schema/` package | New sub-package housing `SchemaViolation` dataclass, validation logic, and re-exports  |

**What does NOT change:**

- No existing public API is modified — `App()`, `@app.device()`, `@app.telemetry()`,
  and `@app.command()` signatures remain identical.
- No existing tests break — schema enforcement is opt-in; without a `schema/asyncapi.yaml`
  file or `COSALETTE_SCHEMA__PATH` setting, the feature is invisible.
- No new required dependencies — AsyncAPI YAML parsing uses `pyyaml` (already a
  transitive dependency via pydantic-settings) and stdlib-only JSON Schema references.

### 4.4 Payload Validation at Publish Time (COS-5hx.9)

This section defines the runtime validation layer that checks outgoing MQTT payloads
against AsyncAPI-defined schemas before they reach the broker. Where §4.3 enforces
_structural correctness_ at startup (are the right topics registered?), this section
enforces _payload correctness_ at runtime (does the published dict match the declared
schema?). This directly implements **UC3 — Payload Validation at Publish Time**.

#### Design Overview

Publish-time validation is an optional wrapper around the `MqttPort.publish()` call.
When **both** conditions are met:

1. A `SchemaRegistry` was loaded during startup (§4.2, §4.3), **and**
2. `settings.schema_.on_publish` is `True` (default: `False`)

…the framework wraps the `MqttPort` instance with a `ValidatingMqttPort` decorator
that intercepts every `publish()` call. The wrapper:

- Looks up the resolved topic in the `SchemaRegistry` via
  `payload_schema_for_topic()`.
- If a payload schema exists, validates the Python dict against the pre-compiled
  `Draft7Validator` from `PayloadValidator` (§4.1).
- Handles violations according to the enforcement mode (`strict`, `warn`, `off`).
- Passes unknown topics (no matching channel in the schema) through without validation —
  the schema is not exhaustive; it only constrains topics the user explicitly declared.

When `on_publish` is `False` or no schema is loaded, the wrapper is never created and
the publish path has **zero overhead**.

#### Validation Interception Point

Two interception strategies were evaluated:

##### Option 1 — ValidatingMqttPort wrapper

Wrap the `MqttPort` protocol implementation with a decorator that intercepts
`publish()`. All framework code that holds a reference to `MqttPort` receives
the wrapper transparently.

| Criterion | Assessment |
| --------- | ---------- |
| **Coverage** | Catches ALL publishes — telemetry, device state, framework-generated messages. Single interception point, no duplication. |
| **Dict availability** | Most paths call `publish()` with a Python dict that is serialized inside the method. For pre-serialized `bytes` paths (availability, LWT), the wrapper skips validation via a topic skip-list. |
| **Coupling** | Zero coupling to `TelemetryRunner` or `DeviceContext` internals — the wrapper only depends on the `MqttPort` protocol. |

##### Option 2 — Pre-serialization hook in TelemetryRunner/DeviceContext

Validate the dict inside `TelemetryRunner._publish()` and `DeviceContext.publish()`
before JSON serialization.

| Criterion | Assessment |
| --------- | ---------- |
| **Coverage** | Misses framework-generated publishes (registry snapshot, error messages) unless additional hooks are added to each call site. |
| **Dict availability** | Dicts are directly available — no deserialization needed. |
| **Coupling** | Requires changes in multiple modules; each new publish path must remember to call validation. |

**Decision: Option 1 (ValidatingMqttPort wrapper).** A single interception point is
easier to audit, cannot be accidentally bypassed when new publish paths are added, and
aligns with the decorator pattern already used for `MqttPort` in the codebase. The
deserialization concern is moot — the wrapper skips validation for the few paths that
publish pre-serialized bytes (availability, LWT) via the topic skip-list.

##### ValidatingMqttPort class

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cosalette._ports import MqttPort
    from cosalette._schema import EnforcementConfig, PayloadValidator, ValidationIssue


class ValidatingMqttPort:
    """MqttPort wrapper that validates payloads against a schema before publishing.

    Wraps an inner ``MqttPort`` and intercepts ``publish()`` to run JSON Schema
    validation when the payload is a dict. Topics in ``_skip_topics`` (error,
    availability, internal) are never validated. Pre-serialized ``bytes``
    payloads are not validated — only dict payloads carry schema semantics.
    """

    def __init__(
        self,
        inner: MqttPort,
        validator: PayloadValidator,
        enforcement: EnforcementConfig,
        error_publisher: ErrorPublisher | None,
        skip_topics: set[str] | None = None,
    ) -> None:
        self._inner = inner
        self._validator = validator
        self._enforcement = enforcement
        self._error_publisher = error_publisher
        self._skip_topics: set[str] = skip_topics or set()
        self._violation_count: int = 0

    async def publish(
        self,
        topic: str,
        payload: bytes | dict,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        if (
            self._enforcement.mode != "off"
            and isinstance(payload, dict)
            and topic not in self._skip_topics
        ):
            issues = self._validator.validate(topic, payload)
            if issues:
                await self._handle_violations(topic, payload, issues)
                if self._enforcement.mode == "strict":
                    return  # suppress publish

        await self._inner.publish(topic, payload, qos, retain)
```

The key design choices:

- **`isinstance(payload, dict)` guard**: Only dict payloads are validated — `bytes`
  payloads (availability strings, LWT) pass through untouched.
- **`topic not in self._skip_topics`**: Error topics, availability topics, and internal
  framework topics are excluded to prevent infinite recursion and unnecessary validation.
- **`strict` mode suppresses**: When `mode == "strict"`, the inner `publish()` is never
  called — the invalid payload never reaches the broker.
- **`_violation_count`**: Simple counter for health reporting (ADR-012). Adapters or
  health checks can inspect this to surface schema health.

#### Performance Considerations

- **Pre-compiled validators.** `Draft7Validator` instances are created once during
  schema loading in `PayloadValidator.__init__()` (§4.1) and reused for every publish.
  No per-publish schema compilation occurs.
- **Topic lookup cost.** `payload_schema_for_topic()` uses a `dict[str, Draft7Validator]`
  lookup — O(1) for exact topic matches — with fallback to template pattern matching
  (O(n) worst case, where n is the number of channels). For typical cosalette apps with
  5–20 topics, the fallback adds negligible overhead.
- **Estimated validation overhead.** A single `Draft7Validator.validate()` call on a
  typical telemetry dict (5–15 keys) runs in ~50–200 μs. Telemetry intervals are
  typically 1–60 seconds (ADR-013) — validation overhead is < 0.01% of cycle time.
- **Fast-path skip.** If enforcement mode is `"off"`, the `publish()` method short-circuits
  on the first boolean check. If no schema is loaded, the wrapper is never instantiated.
  Either way: zero overhead.
- **Bulk telemetry.** For coalescing groups (ADR-018) that publish an aggregated dict
  covering multiple source signals, validation runs once on the final aggregated payload —
  not per-source-signal. This keeps cost constant regardless of group size.
- **Constrained hardware.** On Raspberry Pi deployments, `warn` mode adds a
  `logger.warning()` I/O call per violation, and `strict` mode adds an error-topic
  publish. For production on constrained hardware, `"off"` mode is recommended;
  `"warn"` or `"strict"` are intended for development, CI, or unconstrained hosts.

#### Error Flow

When `_handle_violations()` is called, the behaviour depends on enforcement mode:

##### Strict mode

1. Build a `list[ValidationIssue]` from jsonschema errors (already done by
   `PayloadValidator.validate()`).
2. **Suppress the publish** — `return` before calling `self._inner.publish()`. The
   invalid payload never reaches the broker.
3. **Publish a structured error** to the appropriate error topic via `ErrorPublisher`
   (ADR-011):
   - Device-scoped topics → `{app}/{device}/error`
   - App-level topics → `{app}/error`
4. Log at `ERROR` level:
   `"Schema violation on {topic}: {count} issue(s) — publish suppressed"`
5. Increment `_violation_count` for health reporting (ADR-012).

##### Warn mode

1. Log at `WARNING` level:
   `"Schema violation (warn) on {topic}: {count} issue(s) — publishing anyway"`
2. **Publish the payload** — the message reaches the broker despite violations.
3. Publish a structured error to the error topic (if `error_publisher` is available).
4. Increment `_violation_count`.

##### Off mode

No validation, no logging, no counter increment. The `publish()` method never calls
`_handle_violations()` — the enforcement-mode check short-circuits.

##### Structured error payload

The error published to `{app}/{device}/error` (or `{app}/error`) follows the
existing error payload convention from ADR-011:

```json
{
  "error": "schema_violation",
  "topic": "vito2mqtt/temperature/state",
  "violations": [
    {
      "message": "'temperature' is a required property",
      "path": "$.required"
    },
    {
      "message": "42 is not of type 'string'",
      "path": "$.properties.unit.type"
    }
  ],
  "enforcement": "strict",
  "timestamp": "2026-04-08T12:00:00Z"
}
```

The `violations` array maps directly from `ValidationIssue` dataclass instances (§4.1).
Downstream consumers (Home Assistant, monitoring dashboards) can subscribe to the error
topic to surface schema violations without parsing logs.

#### Lifecycle Wiring

`ValidatingMqttPort` is created in `_run_async()` immediately after `create_mqtt()`
returns the inner `MqttPort` and the `SchemaRegistry` is available from §4.3:

```python
# In App._run_async(), after create_mqtt and schema loading:
mqtt_client = _wiring.create_mqtt(mqtt, resolved_settings, prefix, self._name)

if schema_registry and schema_registry.enforcement.on_publish:
    validator = PayloadValidator(schema_registry)
    skip_topics = _build_skip_topics(prefix, self._devices)
    mqtt_client = ValidatingMqttPort(
        mqtt_client,
        validator,
        schema_registry.enforcement,
        error_publisher,
        skip_topics,
    )

# All downstream code receives mqtt_client — wrapper is transparent.
await mqtt_client.start()
```

Critical sequencing:

- **After `create_mqtt()`**: The inner `MqttPort` must exist to be wrapped.
- **After `load_and_validate_schema()`** (§4.3): The `SchemaRegistry` and its
  `PayloadValidator` must be built from the parsed AsyncAPI document.
- **Before `mqtt_client.start()`**: The validated port must be in place before
  any publish calls occur during the lifespan phase.
- **Transparent substitution**: Because `ValidatingMqttPort` satisfies the `MqttPort`
  protocol, all downstream code — `TelemetryRunner`, `DeviceContext`,
  `AvailabilityPublisher`, `RegistrySnapshotPublisher` — receives the wrapper without
  any signature changes.

The `_build_skip_topics()` helper constructs the skip-list from the app prefix and
device names:

```python
def _build_skip_topics(prefix: str, devices: dict[str, Any]) -> set[str]:
    """Build the set of topics that should bypass publish-time validation."""
    skip = {
        f"{prefix}/error",
        f"{prefix}/status",
    }
    for device_name in devices:
        skip.add(f"{prefix}/{device_name}/error")
        skip.add(f"{prefix}/{device_name}/availability")
    return skip
```

#### What Gets Validated vs. What Doesn't

| Publish Source                     | Validated? | Reason                                                                           |
| ---------------------------------- | ---------- | -------------------------------------------------------------------------------- |
| Telemetry handler return dict      | **Yes**    | Published via `MqttPort.publish()` as a dict — primary UC3 target                |
| `DeviceContext.publish()`          | **Yes**    | Delegates to `MqttPort.publish()` — wrapper intercepts                           |
| Availability messages              | No         | Binary `"online"`/`"offline"` strings (`bytes`), not JSON — `isinstance` guard skips |
| Registry snapshot                  | No         | Internal framework JSON; no user-declared schema. Topic in skip-list             |
| Error messages                     | No         | Error topics are in `_skip_topics` — validating errors about errors causes infinite recursion |
| Health / LWT messages              | No         | Fixed-format binary payloads; topic in skip-list                                 |
| Command acknowledgement publishes  | **Yes**    | If a command handler publishes a response dict, it flows through `MqttPort`      |

The **skip-list mechanism** (`_skip_topics: set[str]`) is the primary filter. It is
built at startup from the known error, availability, and internal topics. The
`isinstance(payload, dict)` check provides a secondary guard — even if a skip-list entry
is missing, `bytes` payloads are never validated.

Together, these two guards ensure:

- **No infinite recursion.** Error publishes triggered by violations are never
  re-validated.
- **No false positives.** Framework-internal payloads (availability, LWT, registry
  snapshot) that have no user-declared schema are never flagged.
- **Comprehensive user-payload coverage.** Every dict published by user code (telemetry
  handlers, device context, command responses) passes through validation if a schema
  exists for the target topic.

#### Thread Safety and Async Considerations

- **`PayloadValidator` is immutable** after construction (`frozen=True` on underlying
  dataclasses, §4.1). The pre-compiled `Draft7Validator` instances are thread-safe for
  read-only use — `jsonschema` performs no internal mutation during validation. Safe
  to share across all async tasks without locks.
- **`ValidatingMqttPort.publish()` is async.** Validation itself is synchronous and
  CPU-bound (< 1 ms for typical payloads). The inner `publish()` call is async
  (I/O-bound — MQTT broker write). The sync validation runs inline in the caller's
  task context; no `run_in_executor()` is needed for sub-millisecond work.
- **No locking required.** Each `publish()` call operates on its own payload dict —
  there is no shared mutable state. The `_violation_count` integer is incremented
  from a single event-loop thread (asyncio is single-threaded), so no atomicity
  concerns arise.
- **Concurrent device tasks.** For apps with many devices (e.g., 50+ temperature
  sensors), each device's `TelemetryRunner` calls `publish()` from its own asyncio
  task. Validation runs in the caller's task context — there is no shared lock, no
  task serialization, and no contention. The only shared state (`_violation_count`)
  is safe under asyncio's cooperative scheduling model.

### 4.5 CLI and Developer Tooling Architecture (COS-5hx.9)

cosalette does not yet have any CLI commands — `App.run()` is the sole entry point, and
developers launch their apps via `python -m myapp` or `uv run myapp`. This section
introduces the **first CLI surface** in the framework: a `cosalette schema` command group
that exposes the developer tooling defined in UC6 (§3.6). The four sub-commands — `init`,
`validate`, `check`, and `dump` — cover the full schema development lifecycle: generating
a starter document, validating it statically, dry-running registration checks against a
live app, and dumping the resolved registry for debugging.

#### CLI Entry Point Design

ADR-005 chose **Typer** as the CLI framework for its type-hint-driven argument parsing
and alignment with cosalette's pydantic-settings philosophy. The schema CLI is the first
consumer of this decision.

**Module location:** `cosalette/_cli.py` (private module — the public entry point is
the `cosalette` console script, not imports from this module).

**Entry point registration** in `pyproject.toml`:

```toml
[project.scripts]
cosalette = "cosalette._cli:app"
```

**Top-level CLI structure:**

```python
# cosalette/_cli.py
from __future__ import annotations

import importlib
import sys
from typing import Any

import typer

app = typer.Typer(
    name="cosalette",
    help="cosalette developer tooling.",
    no_args_is_help=True,
)
schema_app = typer.Typer(
    name="schema",
    help="AsyncAPI schema management commands (UC6).",
    no_args_is_help=True,
)
app.add_typer(schema_app, name="schema")
```

**App import helper.** Several commands need a live `App` instance to introspect
registrations. The `--app` argument follows the uvicorn/gunicorn `module:attribute`
convention:

```python
def _import_app(app_path: str) -> Any:
    """Import a cosalette App instance from a 'module:attribute' string.

    Raises typer.BadParameter if the module cannot be imported or the
    attribute does not exist.
    """
    try:
        module_path, attr_name = app_path.split(":")
    except ValueError:
        raise typer.BadParameter(
            f"Expected 'module:attribute' format, got '{app_path}'"
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise typer.BadParameter(f"Cannot import module '{module_path}': {exc}")
    try:
        return getattr(module, attr_name)
    except AttributeError:
        raise typer.BadParameter(
            f"Module '{module_path}' has no attribute '{attr_name}'"
        )
```

**Invocation forms:**

```text
cosalette schema init --app myapp:app
cosalette schema validate schema/asyncapi.yaml
cosalette schema check --app myapp:app schema/asyncapi.yaml
cosalette schema dump --app myapp:app
```

Because the CLI is installed as a console script, `uv run cosalette schema init ...`
also works without any virtualenv activation.

#### `cosalette schema init` — Generate Starter Schema

**Purpose:** Generate a starter AsyncAPI 3.0.0 YAML document from the app's current
registry snapshot. This is the bootstrapping command — run it once at the start of a
project, then refine the generated YAML with real payload schemas.

**Core generation function** (in `cosalette/_schema_gen.py`):

```python
from __future__ import annotations

from typing import Any


def generate_asyncapi_from_registry(
    snapshot: dict[str, Any],
    *,
    enforcement_mode: str = "warn",
) -> dict[str, Any]:
    """Transform a registry snapshot into an AsyncAPI 3.0.0 document."""
    app_meta = snapshot["app"]
    doc: dict[str, Any] = {
        "asyncapi": "3.0.0",
        "info": {
            "title": app_meta["name"],
            "version": app_meta.get("version", "0.0.0-snapshot"),
            "description": f"Auto-generated schema for {app_meta['name']}.",
        },
        "defaultContentType": "application/json",
        "channels": {},
        "operations": {},
        "x-cosalette-enforcement": {"mode": enforcement_mode},
    }

    # App-level channels
    doc["channels"]["appStatus"] = {
        "address": f"{app_meta['name']}/status",
        "messages": {
            "statusMessage": {
                "payload": {"type": "object"},
            },
        },
    }
    doc["channels"]["appError"] = {
        "address": f"{app_meta['name']}/error",
        "messages": {
            "errorMessage": {
                "payload": {"type": "object"},
            },
        },
    }

    for device in snapshot.get("devices", []):
        _add_device_channels(doc, app_meta["name"], device)
    for telemetry in snapshot.get("telemetry", []):
        _add_telemetry_channel(doc, app_meta["name"], telemetry)
    for command in snapshot.get("commands", []):
        _add_command_channel(doc, app_meta["name"], command)

    return doc
```

**Mapping logic — registry device to AsyncAPI channels:** Each device produces up to
two channels (state + availability) and one or two operations. Telemetry and command
registrations add additional channels:

```python
def _add_device_channels(
    doc: dict[str, Any], app_name: str, device: dict[str, Any]
) -> None:
    """Map a device registration to AsyncAPI channels and operations."""
    name = device["name"]

    # State channel (retained)
    state_id = f"{name}State"
    doc["channels"][state_id] = {
        "address": f"{app_name}/{name}/state",
        "messages": {
            "stateMessage": {
                "payload": {
                    "type": "object",
                    "description": f"Placeholder — refine with actual {name} payload schema.",
                },
            },
        },
    }
    doc["operations"][f"publish{name.title()}State"] = {
        "action": "send",
        "channel": {"$ref": f"#/channels/{state_id}"},
        "x-cosalette-archetype": "device",
    }

    # Availability channel (retained)
    avail_id = f"{name}Availability"
    doc["channels"][avail_id] = {
        "address": f"{app_name}/{name}/availability",
        "messages": {
            "availabilityMessage": {
                "payload": {
                    "type": "string",
                    "enum": ["online", "offline"],
                },
            },
        },
    }
    doc["operations"][f"publish{name.title()}Availability"] = {
        "action": "send",
        "channel": {"$ref": f"#/channels/{avail_id}"},
        "x-cosalette-archetype": "device",
    }
```

**CLI command:**

```python
@schema_app.command()
def init(
    app_path: str = typer.Option(..., "--app", help="App import path (module:attr)."),
    output: str = typer.Option(
        "schema/asyncapi.yaml", "--output", "-o", help="Output file path."
    ),
    enforcement: str = typer.Option(
        "warn", "--enforcement", help="Default enforcement mode."
    ),
) -> None:
    """Generate a starter AsyncAPI 3.0.0 schema from the app's registrations."""
    from pathlib import Path

    import yaml

    from cosalette._introspect import build_registry_snapshot
    from cosalette._schema_gen import generate_asyncapi_from_registry

    user_app = _import_app(app_path)
    snapshot = build_registry_snapshot(user_app)
    doc = generate_asyncapi_from_registry(snapshot, enforcement_mode=enforcement)

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.dump(doc, default_flow_style=False, sort_keys=False))
    typer.echo(f"Schema written to {out_path}")
```

**Example output** (for a `vito2mqtt` app with a single `outdoor_temp` telemetry):

```yaml
asyncapi: "3.0.0"
info:
  title: vito2mqtt
  version: 0.0.0-snapshot
  description: Auto-generated schema for vito2mqtt.
defaultContentType: application/json
x-cosalette-enforcement:
  mode: warn
channels:
  appStatus:
    address: vito2mqtt/status
    messages:
      statusMessage:
        payload:
          type: object
  outdoor_tempState:
    address: vito2mqtt/outdoor_temp/state
    messages:
      stateMessage:
        payload:
          type: object
          description: "Placeholder — refine with actual outdoor_temp payload schema."
operations:
  publishOutdoor_TempState:
    action: send
    channel:
      $ref: "#/channels/outdoor_tempState"
    x-cosalette-archetype: telemetry
```

The developer then refines the placeholder `type: object` payloads with real JSON
Schemas and drops the snapshot version in favour of a semantic version.

#### `cosalette schema validate` — Validate a Schema Document

**Purpose:** Statically validate that an AsyncAPI YAML file is well-formed and that
`x-cosalette-*` extensions conform to their JSON Schemas. No app import is needed —
this is pure document validation suitable for pre-commit hooks and CI.

**Flow:**

1. Load and parse the YAML file.
2. Assert `asyncapi: "3.0.0"` — reject other versions with a clear error.
3. Resolve `$ref` pointers within the document (internal references only).
4. Validate `x-cosalette-enforcement`, `x-cosalette-archetype`,
   `x-cosalette-requires`, and `x-cosalette-coalescing-group` against the JSON
   Schemas defined in §4.1.
5. Report all errors with file path context.
6. Exit code: **0** = valid, **1** = invalid.

**CLI command:**

```python
@schema_app.command()
def validate(
    schema_path: str = typer.Argument(
        ..., help="Path to AsyncAPI YAML file."
    ),
) -> None:
    """Validate an AsyncAPI schema for structural correctness and x-cosalette-* extensions."""
    from pathlib import Path

    from cosalette._schema import load_schema
    from cosalette._schema_validate import validate_asyncapi_document

    path = Path(schema_path)
    if not path.exists():
        typer.echo(f"Error: file not found: {path}", err=True)
        raise typer.Exit(code=1)

    errors = validate_asyncapi_document(path)
    if errors:
        for err in errors:
            typer.echo(f"  ERROR: {err}", err=True)
        typer.echo(f"\n{len(errors)} error(s) found.", err=True)
        raise typer.Exit(code=1)

    typer.echo("Schema is valid.")
```

Because this command needs no app import, it can run in any CI environment —
even containers that lack the app's hardware dependencies.

#### `cosalette schema check` — Dry-Run Registration Validation

**Purpose:** Run the registration validation checks from §4.3
(`_validate_registrations()`) without starting the MQTT broker connection. This is the
**CI gate** command — include it in pipelines to catch schema/registration drift before
deployment.

**Flow:**

1. Import the `App` instance via `--app module:attr`.
2. Construct `SchemaSettings` from environment variables (no broker connection).
3. Expand name specs to finalize the device list (mirrors lifecycle step 5 in §4.3).
4. Load and parse the schema via `load_schema()` from §4.2.
5. Build the `SchemaRegistry` and run `_validate_registrations()` from §4.3.
6. Report violations with topic, expected vs. actual, and severity.
7. Exit code: **0** = all checks pass, **1** = one or more violations found.

**CLI command:**

```python
@schema_app.command()
def check(
    app_path: str = typer.Option(..., "--app", help="App import path (module:attr)."),
    schema_path: str = typer.Argument(
        "schema/asyncapi.yaml", help="Path to AsyncAPI YAML file."
    ),
) -> None:
    """Dry-run schema registration validation (CI gate)."""
    import asyncio
    from pathlib import Path

    from cosalette._introspect import build_registry_snapshot
    from cosalette._schema import SchemaRegistry, load_schema
    from cosalette._schema_check import validate_registrations_offline

    user_app = _import_app(app_path)
    path = Path(schema_path)
    if not path.exists():
        typer.echo(f"Error: schema file not found: {path}", err=True)
        raise typer.Exit(code=1)

    violations = asyncio.run(
        validate_registrations_offline(user_app, path)
    )

    if violations:
        for v in violations:
            typer.echo(f"  {v.severity.upper()}: {v.topic} — {v.message}", err=True)
        typer.echo(f"\n{len(violations)} violation(s) found.", err=True)
        raise typer.Exit(code=1)

    typer.echo("All registrations match the schema.")
```

**The `validate_registrations_offline` helper** reuses the wiring logic from §4.3 but
skips broker connection and adapter initialization:

```python
async def validate_registrations_offline(
    app: App,
    schema_path: Path,
) -> list[Violation]:
    """Run registration validation without starting the full app lifecycle.

    Performs only the steps needed for schema checking:
    1. resolve_settings  (config merge)
    2. expand_name_specs (finalize device names)
    3. load_schema       (parse AsyncAPI)
    4. _validate_registrations (check channels vs. app)
    """
    settings = resolve_settings(app)
    expand_name_specs(
        app._telemetry, app._devices, app._commands, settings
    )
    raw = await load_schema(FileSchemaSource(schema_path))
    registry = SchemaRegistry.from_asyncapi(raw)
    return _validate_registrations(registry, app, settings)
```

**Example CI usage:**

```yaml
# .github/workflows/ci.yml (excerpt)
- name: Schema gate
  run: uv run cosalette schema check --app myapp:app schema/asyncapi.yaml
```

A team that adds a telemetry handler but forgets to update the schema will see:

```text
  WARNING: myapp/humidity/state — Channel exists in app but not in schema
1 violation(s) found.
```

#### `cosalette schema dump` — Debug Output

**Purpose:** Load a schema, build the resolved `SchemaRegistry`, and serialize it to
JSON for debugging. Useful for verifying `$ref` resolution, inspecting which channels
were extracted, and confirming that extension parsing produced the expected data model.

**CLI command:**

```python
@schema_app.command()
def dump(
    app_path: str = typer.Option(..., "--app", help="App import path (module:attr)."),
    schema_path: str = typer.Option(
        "schema/asyncapi.yaml", "--schema-path", help="Path to AsyncAPI YAML."
    ),
    output: str = typer.Option(
        "-", "--output", "-o", help="Output path (- for stdout)."
    ),
) -> None:
    """Dump the resolved SchemaRegistry as JSON for debugging."""
    import asyncio
    from pathlib import Path

    import orjson

    from cosalette._schema import SchemaRegistry, load_schema
    from cosalette._schema_loader import FileSchemaSource

    user_app = _import_app(app_path)
    raw = asyncio.run(load_schema(FileSchemaSource(Path(schema_path))))
    registry = SchemaRegistry.from_asyncapi(raw)

    json_bytes = orjson.dumps(registry.to_dict(), option=orjson.OPT_INDENT_2)

    if output == "-":
        sys.stdout.buffer.write(json_bytes)
        sys.stdout.buffer.write(b"\n")
    else:
        Path(output).write_bytes(json_bytes)
        typer.echo(f"Registry dumped to {output}")
```

**Example:**

```text
$ cosalette schema dump --app vito2mqtt:app | head -20
{
  "channels": {
    "outdoor_tempState": {
      "address": "vito2mqtt/outdoor_temp/state",
      "payload_schema": {"type": "object"},
      "extensions": {
        "x-cosalette-archetype": "telemetry"
      }
    },
    ...
  },
  "enforcement": {
    "mode": "warn"
  }
}
```

#### Project Structure Convention

The default schema location is `schema/asyncapi.yaml` at the project root. This
mirrors the common `schema/` directory convention used by OpenAPI and AsyncAPI tooling.
`cosalette schema init` creates this directory if it does not exist.

```text
myapp/
├── myapp/
│   ├── __init__.py
│   └── app.py          ← App() instance lives here
├── schema/
│   └── asyncapi.yaml   ← generated by `cosalette schema init`
├── pyproject.toml
└── ...
```

The path is overridable at three levels (highest priority first):

1. **CLI flag:** `--schema-path schema/custom.yaml`
2. **Environment variable:** `COSALETTE_SCHEMA__PATH=schema/custom.yaml`
3. **Default:** `schema/asyncapi.yaml`

This follows the same layered-override pattern used by `SchemaSettings` in §4.3.

#### Integration with `task` Commands

The new CLI commands integrate naturally with the project's Taskfile. Proposed entries:

```yaml
# Taskfile.yml additions
schema:init:
  desc: Generate a starter AsyncAPI schema from the app
  cmds: ["uv run cosalette schema init --app {{.CLI_ARGS}}"]

schema:validate:
  desc: Validate the AsyncAPI schema document
  cmds: ["uv run cosalette schema validate schema/asyncapi.yaml"]

schema:check:
  desc: Dry-run schema registration validation (CI gate)
  cmds: ["uv run cosalette schema check --app {{.CLI_ARGS}} schema/asyncapi.yaml"]

schema:dump:
  desc: Dump the resolved SchemaRegistry as JSON
  cmds: ["uv run cosalette schema dump --app {{.CLI_ARGS}}"]
```

The `schema:validate` and `schema:check` tasks can be wired into `task pre-pr` to
enforce schema compliance before creating pull requests:

```yaml
pre-pr:
  cmds:
    - task: lint
    - task: typecheck
    - task: test:unit
    - task: schema:validate
    - task: schema:check
```

This ensures that schema/registration drift is caught at the same stage as lint errors
and type violations — before code leaves the developer's machine.

### 4.6 Testing Strategy (COS-5hx.10)

This section defines the testing approach for the MQTT schema enforcement feature,
covering all modules introduced in §4.1–4.5. The strategy follows ADR-007 (testing
strategy): unit tests for isolated logic, integration tests for lifecycle and
publish-time flows, and property-based tests for payload validation fuzzing.

#### Test Fixture: Sample AsyncAPI Documents

All sample documents live under `tests/fixtures/schemas/`. Each fixture is a
self-contained AsyncAPI 3.0.0 YAML file designed to exercise a specific parser code
path or validation scenario.

| Fixture File               | Purpose                                                                      |
| -------------------------- | ---------------------------------------------------------------------------- |
| `valid_basic.yaml`         | Minimal valid AsyncAPI 3.0.0 — 2 devices, payload schemas, enforcement      |
| `valid_full.yaml`          | Complete document with all `x-cosalette-*` extensions, traits, coalescing    |
| `invalid_version.yaml`     | `asyncapi: 2.6.0` — triggers version gate                                   |
| `invalid_refs.yaml`        | Unresolvable `$ref` pointer (`#/components/schemas/Nonexistent`)             |
| `circular_refs.yaml`       | Circular `$ref` chain (A → B → A)                                            |
| `invalid_extensions.yaml`  | Malformed `x-cosalette-*` values (bad mode, empty capability)                |
| `missing_channels.yaml`    | Valid AsyncAPI structure but empty `channels: {}` section                     |
| `payloads/`                | Sample JSON payloads (valid and invalid) for each fixture schema             |

**Minimal `valid_basic.yaml`:**

```yaml
asyncapi: "3.0.0"
info:
  title: test-app
  version: "1.0.0"
defaultContentType: application/json
x-cosalette-enforcement:
  mode: warn
  hooks:
    on_configure: true
    on_publish: true
channels:
  temperatureState:
    address: "test-app/{deviceName}/state"
    parameters:
      deviceName:
        enum: [temperature, humidity]
    messages:
      stateMessage:
        payload:
          type: object
          required: [value, unit]
          properties:
            value: { type: number }
            unit: { type: string }
          additionalProperties: false
  temperatureSet:
    address: "test-app/{deviceName}/set"
    parameters:
      deviceName:
        enum: [temperature]
    messages:
      commandMessage:
        payload:
          type: object
          required: [target]
          properties:
            target: { type: number }
operations:
  publishTemperature:
    action: send
    channel:
      $ref: "#/channels/temperatureState"
    x-cosalette-archetype: telemetry
  receiveTemperature:
    action: receive
    channel:
      $ref: "#/channels/temperatureSet"
    x-cosalette-archetype: command
```

The `payloads/` subdirectory contains JSON files aligned to each fixture:

- `payloads/temperature_valid.json` — `{"value": 22.5, "unit": "celsius"}`
- `payloads/temperature_invalid_missing.json` — `{"value": 22.5}` (missing `unit`)
- `payloads/temperature_invalid_type.json` — `{"value": "hot", "unit": "celsius"}`
- `payloads/temperature_invalid_extra.json` — `{"value": 22.5, "unit": "celsius", "extra": 1}`

#### Unit Tests: Schema Data Model

Test `_schema.py` classes — pure data model, no I/O. All tests in
`tests/unit/test_schema.py`.

```
Test Techniques Used:
- Specification-based: Constructor contracts, query method return types
- Equivalence Partitioning: Device names — matching vs non-matching
- Boundary Value Analysis: Empty channels dict, empty tag set
- Error Guessing: Frozen mutation, unknown topic lookup
```

| Test Function                                                   | Description                                                                 |
| --------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `test_enforcement_config_defaults`                              | Default values: mode=warn, on_configure=True, on_publish=False              |
| `test_enforcement_config_frozen_immutability`                   | Assigning to a frozen field raises `FrozenInstanceError`                    |
| `test_channel_schema_frozen_immutability`                       | Assigning to any `ChannelSchema` field raises `FrozenInstanceError`         |
| `test_mqtt_binding_defaults`                                    | Default QoS=1, retain=False                                                 |
| `test_channels_for_device_returns_matching_channels`            | Parametrised address template matches the given device name                 |
| `test_channels_for_device_excludes_non_matching`                | Channels for device X do not include channels for device Y                  |
| `test_channels_for_device_includes_concrete_address`            | Channel with device name baked into the address is included                 |
| `test_channels_for_device_empty_registry`                       | Empty `channels` dict returns an empty list                                 |
| `test_required_channels_for_tag_returns_tagged`                 | Channels with matching `x-cosalette-requires` tag are returned              |
| `test_required_channels_for_tag_no_match`                       | Unrecognised tag returns an empty list                                      |
| `test_payload_schema_for_topic_returns_schema`                  | Fully-resolved topic matches template and returns the payload JSON Schema   |
| `test_payload_schema_for_topic_returns_none_for_unknown`        | Topic with no matching channel returns `None`                               |
| `test_topic_matches_parameterised`                              | `_topic_matches` with `{appName}/{deviceName}/state` matches concrete topic |
| `test_topic_matches_concrete`                                   | Concrete address matches itself exactly                                     |
| `test_topic_matches_rejects_mismatch`                           | Non-matching topic returns `False`                                          |

#### Unit Tests: Schema Loader

Test `_schema_loader.py` functions — parsing pipeline, ref resolution, extension
validation. All tests in `tests/unit/test_schema_loader.py`.

```
Test Techniques Used:
- Decision Table: $ref flavour (internal/external/circular) × outcome
- Branch/Condition Coverage: _validate_extensions branches
- Error Guessing: Malformed YAML, missing sections, circular chains
- Equivalence Partitioning: Valid vs invalid extension values
```

| Test Function                                                      | Description                                                                  |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| `test_resolve_refs_internal_pointer`                               | Internal `#/components/schemas/X` is replaced with target content            |
| `test_resolve_refs_nested_internal`                                | Nested `$ref` inside a resolved block is also resolved                       |
| `test_resolve_refs_external_file`                                  | External `./schemas/foo.yaml#/Defs/Bar` loads and inlines correctly          |
| `test_resolve_refs_circular_raises`                                | Circular A → B → A chain raises `SchemaLoadError`                            |
| `test_resolve_refs_unresolvable_raises`                            | `#/components/schemas/Nonexistent` raises `SchemaLoadError`                  |
| `test_resolve_refs_external_without_base_path_raises`              | External ref with `base_path=None` raises `SchemaLoadError`                  |
| `test_extract_channels_parses_address_and_payload`                 | Channel address and first message payload are extracted correctly            |
| `test_extract_channels_missing_channels_section`                   | Document with no `channels` key returns empty dict                           |
| `test_extract_channels_mqtt_binding_defaults`                      | Channel with no `bindings.mqtt` uses `MqttBinding()` defaults               |
| `test_extract_operations_links_channel_ref`                        | Operation's `channel.$ref` resolves to the correct channel key              |
| `test_extract_operations_binding_override`                         | Operation-level MQTT binding overrides channel-level default                 |
| `test_validate_extensions_valid_document`                          | Fully valid extensions return empty error list                               |
| `test_validate_extensions_bad_enforcement_mode`                    | `x-cosalette-enforcement.mode: "yolo"` produces an error string             |
| `test_validate_extensions_empty_capability`                        | `x-cosalette-requires` with empty `capability` produces an error            |
| `test_validate_extensions_bad_coalescing_group`                    | Empty-string coalescing group produces an error                              |
| `test_load_schema_end_to_end_inline`                               | `load_schema(InlineSchemaSource(valid_yaml))` returns a `SchemaRegistry`     |
| `test_load_schema_version_mismatch_raises`                         | Document with `asyncapi: 2.6.0` raises `SchemaLoadError`                    |
| `test_load_schema_malformed_yaml_raises`                           | Invalid YAML syntax raises `SchemaLoadError`                                 |
| `test_load_schema_invalid_extensions_raises`                       | Valid YAML but bad extensions raises `SchemaLoadError` with all errors       |
| `test_schema_load_error_single_message_format`                     | `SchemaLoadError` with one error formats as single-line string               |
| `test_schema_load_error_multi_message_format`                      | `SchemaLoadError` with many errors formats as bulleted list                  |

#### Unit Tests: PayloadValidator

Test `PayloadValidator` — JSON Schema validation against pre-compiled validators.
All tests in `tests/unit/test_payload_validator.py`.

```
Test Techniques Used:
- Equivalence Partitioning: Valid payload, missing field, wrong type, extra property
- Boundary Value Analysis: Empty payload, payload with all optional fields
- Error Guessing: Unknown topic, multiple violations, validator caching
```

| Test Function                                                       | Description                                                                |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `test_validate_valid_payload_returns_empty_list`                    | Dict matching the schema produces no `ValidationIssue`s                    |
| `test_validate_missing_required_field_returns_issue`                | Missing `unit` field produces a `ValidationIssue` with `"required"` path   |
| `test_validate_wrong_type_returns_issue`                            | `{"value": "hot"}` produces a type-mismatch `ValidationIssue`              |
| `test_validate_additional_properties_violation`                     | Extra key with `additionalProperties: false` produces an issue             |
| `test_validate_unknown_topic_returns_empty_list`                    | Topic not matching any channel template returns `[]` (no schema to check)  |
| `test_validate_multiple_violations_aggregated`                      | Payload with 2+ errors returns all `ValidationIssue`s in one list          |
| `test_validate_issue_fields_populated`                              | `channel_name`, `topic`, `message`, and `schema_path` are all set          |
| `test_validator_caching_same_instance_reused`                       | Two calls with the same topic reuse the same `Draft7Validator` instance    |
| `test_validate_empty_payload_against_required_fields`               | `{}` against a schema with required fields returns issues for each         |
| `test_build_validators_invalid_schema_raises`                       | Malformed JSON Schema in a channel raises at construction time             |

#### Unit Tests: ValidatingMqttPort

Test `ValidatingMqttPort` — publish interception and enforcement mode behaviour.
All tests in `tests/unit/test_validating_mqtt_port.py`.

```
Test Techniques Used:
- Decision Table: enforcement mode (strict/warn/off) × payload validity → outcome
- State Transition: violation_count increments
- Error Guessing: bytes payload, skip-topic list, no schema loaded
```

| Test Function                                                       | Description                                                                        |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `test_strict_mode_violation_suppresses_publish`                     | Invalid payload in strict mode → inner `publish()` never called                    |
| `test_strict_mode_violation_calls_error_publisher`                  | Invalid payload in strict mode → `error_publisher` receives structured error       |
| `test_warn_mode_violation_publishes_with_warning`                   | Invalid payload in warn mode → inner `publish()` called, warning logged            |
| `test_off_mode_skips_validation`                                    | Off mode → no validation, inner `publish()` called directly                        |
| `test_valid_payload_publishes_in_all_modes`                         | Valid dict → inner `publish()` called regardless of mode (parametrised)            |
| `test_bytes_payload_bypasses_validation`                            | `bytes` payload → no validation, pass through to inner port                        |
| `test_skip_topic_bypasses_validation`                               | Topic in `skip_topics` set → no validation, pass through                           |
| `test_violation_count_increments`                                   | Each violation increments `_violation_count`                                       |
| `test_no_schema_loaded_passes_through`                              | `PayloadValidator` with empty registry → all publishes pass through                |

#### Integration Tests: Lifecycle

Test the full app lifecycle with schema enforcement using `CosTestHarness`.
All tests in `tests/integration/test_schema_lifecycle.py`.

```
Test Techniques Used:
- State Transition: App startup lifecycle (configure → validate → connect)
- Decision Table: enforcement mode × violation presence → startup outcome
- Specification-based: Settings override behaviour
```

| Test Function                                                               | Description                                                                     |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `test_app_starts_with_valid_schema_warn_mode`                               | Valid schema + warn mode → app starts, `SchemaRegistry` stored on app           |
| `test_app_starts_with_valid_schema_strict_mode_no_violations`               | Valid schema + strict + all registrations match → app starts normally            |
| `test_app_refuses_to_start_strict_mode_with_violations`                     | Valid schema + strict + missing registration → `SchemaViolationError` raised     |
| `test_app_starts_with_warnings_warn_mode_violations`                        | Warn mode + missing registration → app starts, violations logged as WARNING     |
| `test_app_starts_without_schema_enforcement_disabled`                       | No schema file, enforcement off → app starts, `_schema_registry` is `None`      |
| `test_settings_override_document_enforcement_mode`                          | Document says `warn`, settings say `strict` → strict behaviour applies          |
| `test_invalid_schema_strict_mode_raises_load_error`                         | Malformed YAML + strict mode → `SchemaLoadError` raised, app does not start     |
| `test_invalid_schema_warn_mode_starts_without_enforcement`                  | Malformed YAML + warn mode → app starts, warning logged, no registry            |

These tests use `CosTestHarness` with `InlineSchemaSource` to inject schema content
directly, avoiding filesystem dependencies:

```python
async def test_app_starts_with_valid_schema_warn_mode(
    harness: CosTestHarness,
    valid_basic_yaml: str,
) -> None:
    # Arrange
    harness.with_schema(valid_basic_yaml)
    harness.settings.schema_.enforcement = "warn"

    # Act
    async with harness.run() as ctx:
        # Assert
        assert ctx.app._schema_registry is not None
        assert ctx.app._schema_registry.enforcement.mode == "warn"
```

#### Integration Tests: Publish-Time Validation

Test payload validation during actual telemetry publishing via `CosTestHarness`.
All tests in `tests/integration/test_schema_publish.py`.

```
Test Techniques Used:
- Decision Table: payload validity × enforcement mode → publish outcome
- Specification-based: DeviceContext.publish() contract with schema
```

| Test Function                                                                    | Description                                                                     |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `test_valid_telemetry_published_successfully`                                    | Handler returns valid dict → message appears on MQTT mock                       |
| `test_invalid_telemetry_strict_suppresses_publish`                               | Handler returns invalid dict, strict mode → message not published, error topic receives structured error |
| `test_invalid_telemetry_warn_publishes_with_warning`                             | Handler returns invalid dict, warn mode → message published, warning logged     |
| `test_device_context_publish_invalid_strict_suppresses`                          | `DeviceContext.publish()` with invalid payload, strict → suppressed             |
| `test_device_context_publish_invalid_warn_publishes`                             | `DeviceContext.publish()` with invalid payload, warn → published with warning   |

Example test:

```python
async def test_invalid_telemetry_strict_suppresses_publish(
    harness: CosTestHarness,
    valid_basic_yaml: str,
) -> None:
    # Arrange
    harness.with_schema(valid_basic_yaml)
    harness.settings.schema_.enforcement = "strict"
    harness.settings.schema_.on_publish = True

    @harness.app.telemetry("temperature")
    async def read_temp():
        return {"value": "not-a-number", "unit": "celsius"}  # wrong type

    # Act
    async with harness.run() as ctx:
        await ctx.trigger_telemetry("temperature")

    # Assert
    assert ctx.mqtt.published_to("test-app/temperature/state") == []
    assert len(ctx.mqtt.published_to("test-app/error")) == 1
```

#### CLI Tests

Test CLI commands via `typer.testing.CliRunner`. All tests in
`tests/unit/test_cli_schema.py`.

```
Test Techniques Used:
- Specification-based: Exit code contracts per command
- Equivalence Partitioning: Valid vs invalid inputs for each command
- Error Guessing: Missing files, bad app import paths
```

| Test Function                                                   | Description                                                       |
| --------------------------------------------------------------- | ----------------------------------------------------------------- |
| `test_schema_init_generates_valid_yaml`                         | `schema init --app` writes a parseable AsyncAPI YAML file         |
| `test_schema_init_creates_output_directory`                     | Output directory is created if it does not exist                  |
| `test_schema_validate_valid_document_exit_0`                    | Valid fixture file → exit code 0, "Schema is valid." in output    |
| `test_schema_validate_invalid_document_exit_1`                  | Invalid fixture file → exit code 1, error messages in stderr      |
| `test_schema_validate_missing_file_exit_1`                      | Non-existent path → exit code 1, "file not found" error           |
| `test_schema_check_matching_registrations_exit_0`               | All registrations match schema → exit code 0                      |
| `test_schema_check_mismatches_exit_1`                           | Missing registration → exit code 1, violation report in stderr    |
| `test_schema_dump_outputs_valid_json`                           | `schema dump` output is valid JSON containing expected keys       |
| `test_schema_bad_app_path_exit_1`                               | `--app invalid` → exit code 1, `BadParameter` message             |

#### Property-Based Testing with Hypothesis

Hypothesis strategies extend the deterministic test cases above with randomised
inputs. The strategies live in `tests/fixtures/schema_strategies.py` and are imported
by test modules that use `@given`.

**Strategies:**

- `valid_payload_strategy(schema)` — generates random dicts that conform to a given
  JSON Schema (correct types, required fields present, values within bounds).
- `invalid_payload_strategy(schema)` — generates dicts with at least one violation
  (missing required field, wrong type, out-of-range value).
- `random_asyncapi_document()` — produces minimal but structurally valid AsyncAPI 3.0.0
  documents with random channel names and payload schemas. Useful for fuzzing the
  parser; not designed to cover every extension.

**Example tests** in `tests/unit/test_payload_validator_hypothesis.py`:

```python
from hypothesis import given

from tests.fixtures.schema_strategies import (
    invalid_payload_strategy,
    valid_payload_strategy,
)

temperature_schema = {
    "type": "object",
    "required": ["value", "unit"],
    "properties": {
        "value": {"type": "number"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
    },
    "additionalProperties": False,
}


@given(payload=valid_payload_strategy(temperature_schema))
def test_valid_payloads_never_produce_violations(payload):
    """Technique: Property-based — universal property over valid domain."""
    issues = validator.validate("test-app/temperature/state", payload)
    assert issues == []


@given(payload=invalid_payload_strategy(temperature_schema))
def test_invalid_payloads_always_produce_violations(payload):
    """Technique: Property-based — universal property over invalid domain."""
    issues = validator.validate("test-app/temperature/state", payload)
    assert len(issues) > 0
```

The `valid_payload_strategy` uses `hypothesis-jsonschema` (an existing hypothesis
extension) to derive generators directly from JSON Schema definitions, avoiding
hand-rolled generators that drift from the schema.

#### Coverage Targets

| Module                | Target | Rationale                                                    |
| --------------------- | ------ | ------------------------------------------------------------ |
| `_schema.py`          | 95%    | Pure data model — no I/O, no branching complexity            |
| `_schema_loader.py`   | 90%    | Complex parsing; edge cases in `$ref` resolution and error paths |
| `PayloadValidator`    | 95%    | Critical path for enforcement — every branch must be exercised |
| `ValidatingMqttPort`  | 90%    | Integration with `MqttPort` protocol; mode × validity matrix |
| `_cli.py`             | 85%    | CLI testing via `CliRunner` is somewhat brittle across environments |
| Lifecycle integration | 85%    | Tested via `CosTestHarness`; covers startup paths only       |

Coverage is enforced by `task test:cov` (existing gate in `task pre-pr`). New modules
are added to the coverage report automatically — no configuration change needed.

#### Test Harness Extensions

`CosTestHarness` gains four schema-specific helpers:

```python
class CosTestHarness:
    # ... existing helpers ...

    def with_schema(self, yaml_content: str) -> CosTestHarness:
        """Inject an inline AsyncAPI schema for this test.

        Internally creates an ``InlineSchemaSource`` that the wiring
        layer picks up during ``load_and_validate_schema()``. Replaces
        any previously configured schema source.
        """
        self._schema_source = InlineSchemaSource(yaml_content)
        return self

    def with_schema_file(self, path: Path) -> CosTestHarness:
        """Inject a schema from a fixture file.

        Wraps the path in a ``FileSchemaSource``. Useful when testing
        $ref resolution against multi-file fixtures.
        """
        self._schema_source = FileSchemaSource(path)
        return self

    def assert_no_schema_violations(self) -> None:
        """Assert that the last run produced zero schema violations.

        Checks both registration violations (startup) and payload
        violations (publish-time). Raises ``AssertionError`` with a
        detailed violation report on failure.
        """
        violations = self.get_schema_violations()
        assert violations == [], (
            f"Expected no schema violations, got {len(violations)}:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def get_schema_violations(self) -> list[str]:
        """Return all schema violations recorded during the last run.

        Collects violations from:
        - ``_wiring._validate_registrations()`` (startup)
        - ``ValidatingMqttPort._violation_count`` (publish-time)

        Returns human-readable strings suitable for assertion messages.
        """
        violations: list[str] = []
        if self._registration_violations:
            violations.extend(
                f"[registration] {v.message}" for v in self._registration_violations
            )
        if self._publish_violations:
            violations.extend(
                f"[publish] {v.message}" for v in self._publish_violations
            )
        return violations
```

The `with_schema()` method is the primary test interface — it avoids filesystem
dependencies and makes each test self-contained (matching the `InlineSchemaSource`
pattern from §4.2). The `assert_no_schema_violations()` helper provides a single
assertion point that covers both lifecycle phases, reducing boilerplate in integration
tests.

### 4.7 Dependency Analysis

This section catalogues the packages required by schema enforcement, their
relationship to cosalette's existing dependency tree, and the strategy for
keeping the install footprint minimal.

#### New Runtime Dependencies

| Package | Version | Purpose | Size Impact |
|---------|---------|---------|-------------|
| `pyyaml` | `>=6.0` | Parse AsyncAPI YAML documents | ~200 KB |
| `jsonschema` | `>=4.20` | Validate payloads against JSON Schema | ~400 KB (+ transitives) |

**pyyaml** — the mature, battle-tested YAML parser. Version 6.0+ is required for
the `yaml.safe_load()` security hardening and Python 3.14 wheel availability.
Not currently in the dependency tree (only `types-PyYAML` exists in the dev
group). No transitive dependencies — standalone C-extension package.

**jsonschema** — the reference Python implementation for JSON Schema validation.
Version 4.20+ is required for stable Draft 2020-12 support and the
`referencing`-based `$ref` resolver (the legacy `RefResolver` was removed in
4.18). Not currently in the dependency tree. Brings transitive dependencies:
`referencing`, `attrs`, `rpds-py`, and `jsonschema-specifications`.

Both packages are **new** additions — neither is pulled in transitively by
`aiomqtt`, `pydantic`, `orjson`, or `typer`.

#### Optional Runtime Dependencies

| Package | Version | Purpose | When Needed |
|---------|---------|---------|-------------|
| `ruamel.yaml` | `>=0.18` | Round-trip YAML editing | `schema init` CLI — preserves comments and formatting |

`ruamel.yaml` is only needed for the `schema init` command that generates or
patches AsyncAPI YAML. Plain `pyyaml` suffices for read-only parsing. If
`ruamel.yaml` is absent, the CLI can fall back to `pyyaml` with a warning that
comments will not be preserved.

Note: `typer>=0.12` is already a core dependency — no additional install needed
for CLI entry points.

#### Test Dependencies

| Package | Purpose |
|---------|---------|
| `hypothesis-jsonschema` | Generate valid/invalid payloads for property-based testing |

This belongs in the `dev` dependency group alongside the existing `hypothesis`
entry:

```toml
[dependency-groups]
dev = [
    # ... existing entries ...
    "hypothesis-jsonschema>=0.23.1",
]
```

#### Dependency Tree Impact

| Component | Transitive Deps | Installed Size |
|-----------|----------------|----------------|
| `pyyaml` | — (none) | ~200 KB |
| `jsonschema` | `referencing`, `attrs`, `rpds-py`, `jsonschema-specifications` | ~1.5 MB |
| **Total new runtime** | | **~1.7 MB** |

Acceptable for x86/server deployments. For Raspberry Pi (ARM) targets, note
that `rpds-py` ships a Rust extension — pre-built ARM wheels exist on PyPI, but
cross-compilation may be needed for exotic architectures. Monitor in CI.

#### Design Constraints

1. **No AsyncAPI-specific Python libraries.** The `asyncapi` package is immature
   and tightly coupled to code-generation workflows. The architecture
   deliberately avoids it — §4.2 uses raw YAML parsing + `jsonschema` for
   maximum control and minimal surface area.

2. **No heavy frameworks.** Schema enforcement must not pull in web frameworks
   or large transitive trees. `pyyaml` + `jsonschema` together add ~1.7 MB —
   well within budget.

3. **Minimize mandatory deps — use optional extras.** `pyyaml` and `jsonschema`
   are required only when schema enforcement is enabled. They should be gated
   behind an optional extra so bare `pip install cosalette` remains lean:

```toml
[project.optional-dependencies]
schema = ["pyyaml>=6.0", "jsonschema>=4.20"]
```

With a corresponding import guard in `_schema_loader.py`:

```python
try:
    import yaml
except ImportError:
    raise ImportError(
        "pyyaml is required for schema enforcement. "
        "Install with: pip install cosalette[schema]"
    ) from None
```

The same pattern applies for `jsonschema`. Enforcement entry points should fail
fast at import time with an actionable error message rather than at first
validation call.

#### Compatibility Matrix

| Dependency | Python 3.14 | Raspberry Pi (ARM) | Notes |
|-----------|-------------|-------------------|-------|
| pyyaml 6.x | ✓ | ✓ (wheels available) | C extension, well-maintained |
| jsonschema 4.x | ✓ | ✓ (pure Python core) | `rpds-py` has Rust extension with ARM wheels |
| ruamel.yaml 0.18 | ✓ | ✓ (C extension optional) | Falls back to pure Python |
| hypothesis-jsonschema | ✓ | n/a (test-only) | Dev dependency |

All packages are actively maintained with recent releases and broad platform
coverage. No known blockers for the cosalette target matrix.

---

## 5. Implementation Roadmap

This section defines the phased delivery plan for MQTT schema enforcement in
cosalette 0.3.0. Each phase is independently shippable and testable; later phases
build on earlier ones but are separated by clean interface boundaries.

### 5.1 Implementation Phases

#### Phase I — Core Schema Data Model and Loader

| Attribute | Detail |
|-----------|--------|
| **Scope** | `_schema.py` (§4.1) + `_schema_loader.py` (§4.2) |
| **Estimated effort** | 2–3 days |
| **Dependencies** | `pyyaml`, `jsonschema` added as optional extras (`cosalette[schema]`) |

**Deliverables:**

- `SchemaRegistry`, `ChannelSchema`, `OperationSchema`, `EnforcementConfig`,
  `PayloadValidator` dataclasses
- `FileSchemaSource` + `InlineSchemaSource` (§4.2 `SchemaSource` protocol)
- `$ref` resolution pipeline (single-file first, multi-file deferred)
- `x-cosalette-*` extension extraction and validation
- Top-level `load_schema()` function returning a populated `SchemaRegistry`

**Testing:** Unit tests covering data-model construction, YAML parsing, `$ref`
resolution, and extension validation error paths.

**Acceptance criterion:** Can load and parse the vito2mqtt example AsyncAPI
document into a fully-populated `SchemaRegistry`.

---

#### Phase II — Lifecycle Integration and Registration Validation

| Attribute | Detail |
|-----------|--------|
| **Scope** | `_wiring.py` changes, `_settings.py` SchemaSettings, enforcement modes (§4.3) |
| **Estimated effort** | 2–3 days |
| **Depends on** | Phase I |

**Deliverables:**

- `load_and_validate_schema()` wiring function in `_wiring.py`
- `_validate_registrations()` with all five check categories:
  mandatory channels, capability requirements, archetype constraints,
  payload compatibility, topic-pattern matching
- `SchemaSettings` field on the `Settings` model
- `SchemaViolationError`, `SchemaLoadError` exception types
- `strict` / `warn` / `off` enforcement-mode handling

**Testing:** Integration tests using `CosTestHarness` — valid apps start cleanly,
invalid apps in strict mode fail with clear diagnostics.

**Acceptance criterion:** An app with a valid schema starts without warnings; an
app with an invalid schema in strict mode raises `SchemaViolationError` before
MQTT connection.

---

#### Phase III — Publish-Time Payload Validation

| Attribute | Detail |
|-----------|--------|
| **Scope** | `ValidatingMqttPort`, `PayloadValidator` wiring (§4.4) |
| **Estimated effort** | 1–2 days |
| **Depends on** | Phase II |

**Deliverables:**

- `ValidatingMqttPort` wrapper around the real MQTT port
- Wiring into `_run_async()` after `create_mqtt()` (conditionally, based on mode)
- Skip-topic mechanism for topics without payload schemas
- Error reporting via `ErrorPublisher` (§4.4 error flow)

**Testing:** Integration tests for publish-time validation — valid payloads pass
through, invalid payloads trigger error reports (strict) or warnings (warn).

**Acceptance criterion:** A telemetry handler returning an invalid payload triggers
an `ErrorPublisher` report in strict mode, while the correctly-shaped payload
publishes normally.

---

#### Phase IV — CLI and Developer Tooling

| Attribute | Detail |
|-----------|--------|
| **Scope** | `_cli.py` Typer entry point, schema subcommands (§4.5) |
| **Estimated effort** | 2–3 days |
| **Depends on** | Phase I, Phase II |

**Deliverables:**

- `cosalette schema init` — generate AsyncAPI YAML from a live registry snapshot
- `cosalette schema validate` — static document validation (syntax + extension rules)
- `cosalette schema check` — dry-run registration validation against a document
- `cosalette schema dump` — debug JSON output of the resolved `SchemaRegistry`
- `Taskfile.yml` entries: `task schema:init`, `task schema:validate`,
  `task schema:check`

**Testing:** CLI tests with Typer `CliRunner`, snapshot fixtures for expected
outputs.

**Acceptance criterion:** `cosalette schema init --app vito2mqtt:app` produces a
valid AsyncAPI 3.0.0 YAML document that passes `cosalette schema validate`.

---

#### Gantt-Style Timeline (Indicative)

```
Week 1            Week 2            Week 3            Week 4
──────────────────────────────────────────────────────────────
Phase I  ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Phase II ░░░░░░░░░░░░████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░
Phase III░░░░░░░░░░░░░░░░░░░░░░░░█████████░░░░░░░░░░░░░░░░░░
Phase IV ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░████████████░░░░░░
ADR      ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Docs     ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░████████
──────────────────────────────────────────────────────────────
█ = active   ░ = waiting / idle
```

Phases I and II are the critical path. Phase IV can begin as soon as Phase I
completes (the CLI `validate`/`init` commands only need the loader); the `check`
command additionally requires Phase II. The ADR can be drafted at any time and
finalised after Phase II proves the design.

### 5.2 Beads Task Structure

Implementation tasks will be children of a new implementation epic, linked as a
follow-on to the evaluation epic COS-5hx.

```
COS-xxx  Epic: MQTT Schema Enforcement Implementation
├── COS-xxx.1   Phase I: Schema data model (_schema.py)
├── COS-xxx.2   Phase I: Schema loader (_schema_loader.py)
├── COS-xxx.3   Phase I: Unit tests for data model and loader
├── COS-xxx.4   Phase II: SchemaSettings on Settings model
├── COS-xxx.5   Phase II: Registration validation (_validate_registrations)
├── COS-xxx.6   Phase II: Lifecycle wiring (load_and_validate_schema)
├── COS-xxx.7   Phase II: Integration tests for lifecycle
├── COS-xxx.8   Phase III: ValidatingMqttPort
├── COS-xxx.9   Phase III: Publish-time integration tests
├── COS-xxx.10  Phase IV: CLI entry point and schema init
├── COS-xxx.11  Phase IV: schema validate/check/dump commands
├── COS-xxx.12  Phase IV: CLI tests
├── COS-xxx.13  ADR: MQTT schema enforcement decision record
├── COS-xxx.14  Documentation: schema enforcement guide
└── COS-xxx.15  Reference schema: vito2mqtt AsyncAPI document
```

#### Dependency Graph

```
.1 ──────┬──→ .2 ──→ .3
         │
         ├──→ .4 ──┐
         │         ├──→ .6 ──┬──→ .7
         └──→ .5 ──┘         │
                              ├──→ .8 ──→ .9
                              │
         .1 ──→ .2 ──→ .10 ──┤
                              ├──→ .11 ──→ .12
                              │
                   .6 ────────┤
                              ├──→ .14
                   .11 ───────┘

         .10 ──→ .15

         .13  (no dependencies — can be authored at any point)
```

Linearised dependency table:

| Task | Depends on |
|------|-----------|
| .1 | — |
| .2 | .1 |
| .3 | .1, .2 |
| .4 | .1 |
| .5 | .1 |
| .6 | .4, .5 |
| .7 | .6 |
| .8 | .6 |
| .9 | .8 |
| .10 | .1, .2 |
| .11 | .10, .6 |
| .12 | .11 |
| .13 | — |
| .14 | .6, .11 |
| .15 | .10 |

### 5.3 Risk Register

| # | Risk | Prob. | Impact | Mitigation |
|---|------|-------|--------|------------|
| R1 | `jsonschema` validation too slow on Raspberry Pi | Low | Medium | Pre-compiled validators (§4.4), `off` mode for production deployments |
| R2 | AsyncAPI 3.0.0 spec evolves; `x-cosalette-*` extensions break | Low | High | Pin format to `3.0.0`, fallback path to Option C (§2.3) if needed |
| R3 | `$ref` resolution fails on complex multi-file schemas | Medium | Low | Start with single-file; add multi-file support in a later phase |
| R4 | Developer adoption friction — schema feels like overhead | Medium | High | Good defaults (`warn` mode), `schema init` generator, clear docs |
| R5 | Schema/registration drift in CI — `check` command not run | Low | Medium | Wire `cosalette schema check` into `task pre-pr`, make it a CI gate |
| R6 | Coalescing-group validation logic too tightly coupled | Low | Low | Test validation checks in isolation, keep check functions modular (§4.3) |

**Risk heat-map:**

```
        Low Impact    Medium Impact    High Impact
High P  ·             ·                ·
Med P   ·             R3               R4
Low P   R6            R1, R5           R2
```

R2 and R4 are the most consequential risks. R2 is mitigated structurally by
version-pinning and the Option C escape hatch. R4 is mitigated by developer
experience investment (Phase IV) and defaulting to `warn` mode so adoption is
incremental.

### 5.4 Success Criteria

The MQTT schema enforcement feature is **done** when all of the following hold:

1. A cosalette app can opt into schema enforcement by placing an AsyncAPI YAML
   document at `schema/asyncapi.yaml` (or configuring a custom path via
   `SchemaSettings`).
2. At startup, device and app-level registrations are validated against the schema
   — mandatory channels, capability requirements, archetype constraints, and
   payload compatibility (§4.3).
3. In **strict** mode, schema violations raise `SchemaViolationError` and prevent
   the app from starting.
4. In **warn** mode, violations are logged at `WARNING` level but the app starts
   normally.
5. Payload validation (when enabled) catches type and shape errors **before** MQTT
   publish, reporting via `ErrorPublisher` (§4.4).
6. `cosalette schema init` generates a valid starter AsyncAPI document from any
   cosalette app's registry snapshot.
7. `cosalette schema check` works in CI pipelines as a quality gate (exit code 0
   on pass, non-zero on violations).
8. All implementation is covered by tests meeting the project's coverage targets
   (unit, integration, and property-based tests per §4.6).
9. An ADR documenting the decision is published in `docs/adr/`.
10. A developer guide with worked examples is published in `docs/guides/`.

### 5.5 ADR Scope (COS-5hx.11)

The ADR will be authored using the `adr-create` skill and will reference this
planning document for full evaluation context.

| Field | Value |
|-------|-------|
| **Title** | MQTT Schema Enforcement |
| **Impact** | High |
| **Status** | Proposed (→ Accepted after implementation proves the design) |

**Decision:** Adopt AsyncAPI 3.0.0 with `x-cosalette-*` custom extensions as the
schema format for MQTT topic and payload enforcement.

**Context summary (from §1–§3):**

- cosalette enforces topic _structure_ by construction but not payload _content_
- Three format options were evaluated: AsyncAPI (A), JSON Schema bundle (B),
  custom YAML DSL (C)
- AsyncAPI scored highest on ecosystem, tooling reuse, and expressiveness
- Extensions map cleanly to cosalette concepts (archetypes, capabilities,
  coalescing groups)

**Consequences:**

- New modules: `_schema.py`, `_schema_loader.py`, `ValidatingMqttPort`
- New optional dependencies: `pyyaml`, `jsonschema` (§4.7)
- Lifecycle change: schema loading and validation injected between configuration
  resolution and MQTT connection
- CLI surface grows by four subcommands
- Enforcement is opt-in and off by default — existing apps are unaffected

### 5.6 Open Questions

The following questions remain open for resolution during implementation:

1. **Optional vs required dependency group.** Should `cosalette[schema]` be an
   optional extra or a required dependency? Current leaning: **optional**, to keep
   the core package slim for production deployments that run with `off` mode.

2. **Sync vs async validation.** The loader needs async I/O for potential future
   HTTP schema sources. Validation itself is synchronous CPU work. Should the
   public API be `async def` throughout, or sync with an async loader entry point?
   Current leaning: **async loader, sync validator**, composed in an async wiring
   function.

3. **Dry-run mode interaction.** How does schema enforcement interact with
   cosalette's dry-run mode? Proposed: validate registrations and payloads
   normally (catching errors early), but skip the MQTT connection — same as
   current dry-run behaviour with an added validation pass.

4. **`schema init` payload detail level.** Should `schema init` generate
   device-specific payload schemas (by inspecting return-type annotations) or emit
   `type: object` placeholders? Current leaning: **placeholders first**, with
   annotated-type introspection as a follow-on enhancement.

5. **Extension schema versioning.** Should the `x-cosalette-*` extension schema be
   versioned independently from the cosalette package version? Current leaning:
   **yes** — a `x-cosalette-schema-version` field in the document root allows the
   extension format to evolve without requiring a cosalette major bump.
