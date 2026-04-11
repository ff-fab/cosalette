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

`init` introspects the running app and produces an AsyncAPI 3.0.0 document:

```yaml
asyncapi: 3.0.0
info:
  title: thermo2mqtt
  version: 0.1.0

x-cosalette-enforcement:
  mode: warn
  on_configure: true
  on_publish: false
  network_level: false

channels:
  temperatureState:
    address: thermo2mqtt/temperature/state
    x-cosalette-archetype: telemetry
    messages:
      message:
        payload:
          type: object

  setpointCommand:
    address: thermo2mqtt/setpoint/set
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

The scaffolded payloads are `type: object` — add properties and constraints by hand.

!!! tip "`init` vs `dump`"

    - `cosalette schema init` — includes `x-cosalette-enforcement` and per-channel
      archetype annotations. Use this to create a schema you will commit and validate
      against.
    - `cosalette schema dump` — produces the minimal AsyncAPI document without
      cosalette extensions. Use this to generate a schema for external tooling
      (AsyncAPI Studio, documentation generators).

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

Add the schema path to your app's configuration:

```python
from cosalette import App, Settings

class MySettings(Settings):
    schema_path: str = "/etc/cosalette/thermo2mqtt-schema.yaml"

app = App(name="thermo2mqtt", settings_class=MySettings)
```

Or in `.env` / environment:

```env
SCHEMA_PATH=/etc/cosalette/thermo2mqtt-schema.yaml
```

The framework reads `x-cosalette-enforcement.mode` from the schema file and applies
it at startup (`on_configure: true`) or per-publish (`on_publish: true`).

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

  # Fleet-wide channel — all apps publish on this pattern
  appAvailability:
    address: "{appName}/availability"
    x-cosalette-scope: all_apps
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
| `x-cosalette-app` | `string` | App name that owns this channel. Required for network schemas. |
| `x-cosalette-archetype` | `string` | One of `device`, `telemetry`, `command`. |
| `x-cosalette-scope` | `string` | `all_apps` — channel is shared across all apps (e.g. availability). |
| `x-cosalette-coalescing-group` | `string` | [Coalescing group](../concepts/coalescing-groups.md) this channel belongs to. |
| `x-cosalette-requires` | `list` | Capability tag requirements (see ADR-014). |

### Property-level extensions

| Extension | Type | Description |
|-----------|------|-------------|
| `x-cosalette-consumer` | `object` | Consumer metadata for downstream integration code generation (home automation, dashboards, etc.). |
| `x-cosalette-consumer.device_class` | `string` | Semantic device class consumed by integrations (e.g. `temperature`, `battery`). |
| `x-cosalette-consumer.unit` | `string` | Unit string for display in consumer integrations. |
| `x-cosalette-consumer.display_name` | `string` | Human-readable name for the property. |
| `x-cosalette-consumer.state_class` | `string` | `measurement`, `total`, `total_increasing`. |

### Document-level enforcement config

```yaml
x-cosalette-enforcement:
  mode: warn          # off | warn | strict
  on_configure: true  # validate at startup
  on_publish: false   # validate payload at publish time
  network_level: false
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `cosalette schema validate <file>` | Validate schema document structure. |
| `cosalette schema check --app module:attr --schema <file>` | Check app registrations against schema (CI gate). |
| `cosalette schema dump --app module:attr` | Generate minimal AsyncAPI YAML from app's registry. |
| `cosalette schema init --app module:attr` | Generate starter schema with cosalette extensions (for editing). |
| `cosalette schema slice --network <file> --app <name>` | Extract one app's slice from a network schema. |

---

## Further Reading

- [ADR-033 — MQTT Schema Enforcement](../adr/ADR-033-mqtt-schema-enforcement.md) —
  decision record: format choice, distribution model, enforcement modes.
- [Reference Network Schema](../assets/reference-network-schema.yaml) — annotated
  three-app fleet schema.
