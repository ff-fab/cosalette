# COS-8zj: Consumer Code Generation

**Status:** Draft — awaiting approval\
**Ref:** ADR-033, beads COS-8zj\
**Depends on:** Phase I (COS-apf, closed), Phase II (COS-6yf, closed), Phase III
(COS-grj, closed)

## Problem Statement

cosalette apps define their MQTT topic/payload structure via device registrations and
AsyncAPI schemas. Today, integrating these apps with consumer platforms (Home Assistant,
OpenHAB) requires **hand-maintaining** discovery payloads and configuration files. This
is error-prone and drifts from the schema over time.

Phase IV generates consumer configuration **from the schema**, making the schema the
single source of truth for both enforcement and downstream integration.

## Deliverables

1. `_consumer_gen.py` module with `HaDiscoveryGenerator` and `OpenHabGenerator`
2. CLI commands: `cosalette schema ha-discovery` and `cosalette schema openhab`
3. Unit tests with ISTQB technique documentation
4. Test fixture YAML with `x-cosalette-ha-discovery` and `x-cosalette-openhab`
   annotations

## Acceptance Criteria

- Generated HA discovery payloads accepted by HA when published to MQTT
- OpenHAB `.things`/`.items` files syntactically valid
- All quality gates pass (`task pre-pr`)

---

## Design Decision 1: Module Structure

### Option A: Flat Functions (ACL Pattern)

Follow the existing `_acl.py` pattern exactly — standalone functions and a data
class for output.

```python
def derive_ha_discovery(registry, prefix="homeassistant") -> list[HaDiscoveryPayload]: ...
def derive_openhab_things(registry, broker_uid="broker") -> str: ...
def derive_openhab_items(registry, broker_uid="broker") -> str: ...
```

- **Advantages:** Consistent with `_acl.py`; pure functions; simple; easy to test
- **Disadvantages:** Generators share no config state so related parameters are repeated;
  doesn't match issue spec naming ("HaDiscoveryGenerator")

### Option B: Thin Generator Classes with Frozen Config

Classes hold configuration (discovery prefix, broker UID), `generate()` returns
immutable data.

```python
@dataclass(frozen=True, slots=True)
class HaDiscoveryGenerator:
    registry: SchemaRegistry
    discovery_prefix: str = "homeassistant"

    def generate(self) -> list[HaDiscoveryPayload]: ...

@dataclass(frozen=True, slots=True)
class OpenHabGenerator:
    registry: SchemaRegistry
    broker_uid: str = "broker"

    def generate_things(self) -> str: ...
    def generate_items(self) -> str: ...
```

- **Advantages:** Matches issue spec naming; groups config with operation; extensible
  (publish method later); frozen dataclass keeps immutability
- **Disadvantages:** Slightly more ceremony than plain functions

**Recommendation:** Option B. The issue explicitly requests these class names, frozen
dataclasses are idiomatic in this codebase, and the classes provide a natural home for
future `publish()` methods (ADR-033 mentions an MQTT publish CLI). The slight extra
ceremony is worth the clarity.

---

## Design Decision 2: HA Discovery Topic and Payload Structure

### HA MQTT Discovery Protocol

Home Assistant listens for retained messages on:

```
{discovery_prefix}/{component}/{node_id}/{object_id}/config
```

**Mapping from schema:**

| HA Field               | Source                                          |
| ---------------------- | ----------------------------------------------- |
| `component`            | `ha_discovery.component` or inferred from       |
|                        | archetype + JSON schema type                    |
| `node_id`              | `channel.app_name`                              |
| `object_id`            | `{device}_{property_name}` (slugified)          |
| `name`                 | `consumer.display_name` or property name        |
| `device_class`         | `consumer.device_class`                         |
| `unit_of_measurement`  | `consumer.unit`                                 |
| `state_class`          | `consumer.state_class`                          |
| `state_topic`          | `channel.address` (for send channels)           |
| `command_topic`        | `channel.address` (for receive channels)        |
| `value_template`       | `ha_discovery.value_template` or auto-generated |
|                        | `{{ value_json.<property_name> }}`              |
| `command_template`     | `ha_discovery.command_template`                 |
| `expire_after`         | `ha_discovery.expire_after`                     |
| `unique_id`            | `cosalette_{app}_{device}_{property}`           |
| `icon`                 | `consumer.icon`                                 |
| `device.identifiers`   | `["cosalette_{app_name}"]` or per-device        |
| `device.name`          | `app_name`                                      |
| `device.manufacturer`  | `"cosalette"`                                   |

### Component Inference

When `ha_discovery.component` is not set, infer from context:

| Archetype  | JSON type    | Component       |
| ---------- | ------------ | --------------- |
| telemetry  | number       | `sensor`        |
| telemetry  | string       | `sensor`        |
| telemetry  | boolean      | `binary_sensor` |
| command    | boolean      | `switch`        |
| command    | integer      | `number`        |
| command    | number       | `number`        |
| command    | string+enum  | `select`        |
| device     | *(any)*      | `sensor`        |

### Device Grouping

Properties from the same `{app}/{device}/state` channel share a `device` block.
Channels with `{deviceName}` template produce one device per known device name
from `registry.device_names`. If `device_names` is empty but the address contains
`{deviceName}`, we emit a template placeholder and document that the user should
resolve it.

---

## Design Decision 3: OpenHAB Output Format

### Things File

```java
// Generated by cosalette schema openhab
// Source: network_basic.yaml

Thing mqtt:topic:broker:vito2mqtt_temperature "vito2mqtt temperature" (mqtt:broker:broker) {
    Channels:
        Type number : temperature "Heating Water Temperature" [
            stateTopic="vito2mqtt/temperature/state",
            transformationPattern="JSONPATH:$.temperature"
        ]
}
```

### Items File

```java
// Generated by cosalette schema openhab

Number:Temperature  Vito2mqtt_Temperature_Temperature  "Heating Water Temperature [%.1f °C]"  <temperature>  (gVito2mqtt)  ["Measurement", "Temperature"]  { channel="mqtt:topic:broker:vito2mqtt_temperature:temperature" }
```

### Type Mapping

| `consumer.device_class` | OpenHAB `item_type`       | Format Pattern    |
| ------------------------ | ------------------------- | ----------------- |
| `temperature`            | `Number:Temperature`      | `%.1f °C`         |
| `humidity`               | `Number:Dimensionless`    | `%.0f %%`         |
| `carbon_dioxide`         | `Number:Dimensionless`    | `%d ppm`          |
| *(fallback number)*      | `Number`                  | `%s`              |
| *(fallback string)*      | `String`                  | `%s`              |
| *(fallback boolean)*     | `Switch`                  | `%s`              |

The `openhab.item_type` override takes precedence when set.

---

## Design Decision 4: CLI Commands

Following the existing `acl` command pattern in `_cli.py`:

```
cosalette schema ha-discovery <schema-path> [--prefix homeassistant] [--format json|yaml]
cosalette schema openhab <schema-path> [--broker-uid broker] [--output things|items|both]
```

- `ha-discovery` outputs JSON array of `{topic, config}` objects (one per entity)
- `openhab --output things` outputs `.things` syntax
- `openhab --output items` outputs `.items` syntax
- `openhab --output both` outputs both sections separated by a header comment

These follow the pattern of `acl`: load schema, call generator, format output, echo.

---

## File Plan

| File                                                  | Purpose                                |
| ----------------------------------------------------- | -------------------------------------- |
| `packages/src/cosalette/_schema/_consumer_gen.py`     | Generator classes + output dataclasses |
| `packages/tests/unit/test_schema_consumer_gen.py`     | Unit tests                             |
| `packages/tests/fixtures/schemas/consumer_basic.yaml` | Test fixture with all consumer         |
|                                                       | annotations                            |

CLI commands will be added to the existing `_schema/_cli.py` (following the `acl`
command pattern — lazy import, call generator, echo output).

---

## Test Plan

### Techniques

| Test Case                          | ISTQB Technique             |
| ---------------------------------- | --------------------------- |
| Telemetry property → sensor        | Equivalence Partitioning    |
| Command property → switch/number   | Equivalence Partitioning    |
| Explicit component override        | Boundary Value Analysis     |
| Missing consumer metadata skipped  | Boundary Value Analysis     |
| Multi-property channel             | Branch Coverage             |
| Device grouping by app_name        | Specification-based Testing |
| OpenHAB type mapping               | Equivalence Partitioning    |
| Empty registry → empty output      | Boundary Value Analysis     |
| value_template auto-generation     | Specification-based Testing |
| CLI output format                  | Round-trip Testing          |

### Test Structure

```
TestHaDiscoveryGenerator
    test_generate_sensor_from_telemetry_channel
    test_generate_binary_sensor_from_boolean_property
    test_generate_switch_from_command_boolean
    test_generate_number_from_command_integer
    test_generate_explicit_component_override
    test_generate_value_template_auto
    test_generate_value_template_explicit
    test_generate_device_grouping
    test_generate_skips_property_without_consumer
    test_generate_empty_registry
    test_generate_unique_id_format
    test_generate_expire_after

TestOpenHabGenerator
    test_things_from_telemetry_channel
    test_items_from_telemetry_channel
    test_items_type_mapping_temperature
    test_items_type_mapping_humidity
    test_items_explicit_type_override
    test_things_command_channel
    test_empty_registry
    test_groups_and_tags

TestConsumerGenCli
    test_ha_discovery_command_output
    test_openhab_things_command_output
    test_openhab_items_command_output
```

---

## Next Steps

1. Approve or adjust this plan
2. Create feature branch from `main`
3. Implement `_consumer_gen.py` (generators + output dataclasses)
4. Add test fixture YAML
5. Write unit tests
6. Add CLI commands to `_cli.py`
7. Run `task pre-pr`, create PR
