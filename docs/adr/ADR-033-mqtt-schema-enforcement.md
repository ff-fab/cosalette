---
status: Accepted
date: 2026-04-09
impact: high
tags: [mqtt, architecture, cli, dependencies, security]
---

# ADR-033: MQTT Schema Enforcement

## Status

Accepted **Date:** 2026-04-09

## Context

cosalette enforces MQTT topic conventions by code (ADR-002) and framework behaviour: devices automatically publish to `{app}/{device}/state`, subscribe to `{app}/{device}/set`, and announce availability on `{app}/{device}/availability`. The _structure_ is correct by construction, but five gaps remain:

1. **No payload shape enforcement.** A telemetry handler returning `{"temp": 22.5}` today and `{"temperature": 22.5}` tomorrow is not caught — downstream consumers silently break.
2. **No capability-based requirements.** No way to declare "every device tagged `battery_powered` must publish a `/battery` topic."
3. **No machine-readable contract.** Monitoring tools, code generators, and documentation systems cannot discover which topics an app produces or consumes.
4. **No validation mode for development.** Typos and type mismatches in payloads surface only when an MQTT consumer fails.
5. **No cross-app contract.** The network of ~20 cosalette apps across ~6 Raspberry Pi hosts has no collective validation. Renaming a device in one app silently breaks Home Assistant automations, Grafana dashboards, and other apps that subscribe to the old topic.

The fifth gap is the **primary value driver** for the target deployment: a solo operator running ~20 apps across ~6 hosts managed by Ansible. Per-app validation is bounded in value when the same person writes the app and the schema. Cross-app validation — catching deployment regressions before they propagate — is where the highest return lies.

The deployment uses Ansible for configuration management, Mosquitto as the MQTT broker, and Home Assistant plus Grafana as primary consumers. Schema enforcement must integrate with the existing `on_configure` lifecycle phase (ADR-023) and the decorator-based device registration model (ADR-010).

## Decision

Use **AsyncAPI 3.0.0** as the schema format with `x-cosalette-*` extensions, adopting a **network-first** distribution model where a single central schema defines the entire MQTT topology and each app validates against its relevant slice.

**Schema format:** AsyncAPI 3.0.0 provides first-class MQTT channel definitions, JSON Schema payloads, operation bindings (QoS, retain), and an extension mechanism (`x-`) for cosalette-specific metadata (app ownership, device archetypes, consumer hints for HA/OpenHAB code generation).

**Distribution:** Ansible deploys the network schema file to `/etc/cosalette/network-schema.yaml` on all hosts. An optional MQTT reload signal on `cosalette/schema/update` triggers running apps to re-read the local file without restart. The MQTT message is a hint only — the authoritative schema is always the local file.

**Enforcement modes:** `off` (default — zero operational burden), `warn` (log violations, continue), `strict` (fail startup on violation). Gated behind optional extras: `pip install cosalette[schema]` pulls `pyyaml` and `jsonschema`.

**Authorization:** Broker ACLs with unique per-app MQTT principals enforce topic ownership for control topics. Signed messages are not required in v1.

**CLI:** `cosalette schema validate|check|dump|init|slice|acl` subcommands for static validation, CI gating, schema bootstrapping, and broker ACL generation.

## Decision Drivers

- Cross-app contract validation must catch deployment regressions before they break consumers (HA, Grafana, inter-app subscriptions)
- Schema format must express MQTT-specific semantics (QoS, retain, topic patterns) natively, not as ad-hoc annotations
- Distribution must work offline on Raspberry Pi hosts managed by Ansible without requiring HTTP infrastructure or broker availability at startup
- Zero adoption friction for users who do not enable enforcement — no new dependencies, no new topics, no broker configuration
- CLI tooling must support CI integration (pre-deploy validation gate) and consumer code generation (HA discovery, OpenHAB configs)
- Authorization model must prevent unauthorized reload triggers and compliance spoofing on shared brokers

## Considered Options

### Option 1: AsyncAPI 3.0.0 with x-cosalette extensions (network-first) (chosen)

Use AsyncAPI 3.0.0 as the schema format. One central network schema defines all apps' channels, payloads, and MQTT bindings. Each app filters to its own slice at startup. Custom `x-cosalette-app`, `x-cosalette-archetype`, `x-cosalette-consumer`, and `x-cosalette-ha-discovery` extensions carry framework-specific metadata. Distribution via Ansible file deployment with optional MQTT reload hint.

- *Advantages:* Industry-standard format with first-class MQTT support (channels, operations, MQTT bindings for QoS and retain); JSON Schema payloads enable both static validation (CI) and runtime validation (publish-time); Extension mechanism (`x-`) is part of the spec — no forking or patching needed for cosalette metadata; Existing tooling ecosystem (AsyncAPI Studio for editing, code generation libraries, documentation renderers); Network-first model catches cross-app regressions — the primary value driver for the target deployment; Single source of truth enables consumer code generation (HA discovery, OpenHAB configs) from the same schema
- *Disadvantages:* AsyncAPI is verbose for simple schemas — a 3-device app requires ~100 lines of YAML; Conditional capability enforcement (e.g. 'if battery_powered then must have battery topic') requires custom validation logic beyond what AsyncAPI expresses natively; Two new runtime dependencies (pyyaml ~200KB, jsonschema ~400KB + transitives) — manageable but non-zero on constrained targets; AsyncAPI 3.0.0 tooling maturity in Python is limited — the schema loader must be built from scratch rather than using an off-the-shelf parser

### Option 2: JSON Schema + custom YAML manifest

Plain JSON Schema files for payload validation paired with a lightweight custom YAML manifest for topic requirements and capability rules. No industry-standard envelope — cosalette defines its own topology format.

- *Advantages:* Full control over format — can express capability enforcement natively without extensions; Simpler schema files for common cases (less verbose than AsyncAPI); jsonschema is the only runtime dependency (pyyaml optional if using JSON format); Payload validation is identical in both approaches — JSON Schema is the payload format regardless
- *Disadvantages:* No MQTT-specific semantics (QoS, retain, topic patterns) in the format — must be reinvented; No ecosystem tooling — no editor support, no documentation generators, no code generation; Custom format creates a learning curve unique to cosalette with no transferable knowledge; Interoperability with external tools (AsyncAPI Studio, Spectral linters) is lost

### Option 3: Custom YAML DSL

Purpose-built cosalette schema format with native concepts for device archetypes, capability tags, topic patterns, and payload shapes. Maximum expressiveness, zero compromise with external standards.

- *Advantages:* Highest expressiveness — can model cosalette concepts (archetypes, coalescing groups, capability tags) directly; Most concise representation of common patterns; No external spec dependency — format evolves with the framework; Capability enforcement is a first-class concept, not an extension
- *Disadvantages:* Must build and maintain parser, validator, documentation generator, and editor tooling from scratch; Zero transferable knowledge for users — every concept is cosalette-specific; No interoperability with any external tool or standard; Risk of reinventing AsyncAPI poorly — MQTT semantics will need to be represented eventually

## Decision Matrix

| Criterion | AsyncAPI 3.0.0 with x-cosalette extensions (network-first) | JSON Schema + custom YAML manifest | Custom YAML DSL |
| --- | --- | --- | --- |
| Expressiveness (MQTT semantics, topic patterns, payload shapes) | 4 | 4 | 5 |
| Payload validation (JSON Schema integration, pre-compiled validators) | 5 | 5 | 5 |
| Ecosystem tooling (editor support, documentation, code generation) | 4 | 3 | 1 |
| Interoperability (external tools, transferable knowledge) | 5 | 2 | 1 |
| Runtime footprint (dependency size, Pi suitability) | 3 | 4 | 5 |
| Implementation complexity (parser, loader, validator) | 3 | 4 | 2 |
| Evolvability (schema versioning, backward compatibility, extension growth) | 3 | 4 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- The MQTT topology across all ~20 apps is machine-readable from a single file — answering 'what topics does my system produce?' no longer requires reading source code
- Ansible pre-deploy validation gates catch regressions (renamed devices, missing topics) before they break Home Assistant automations and Grafana dashboards
- Consumer code generation (HA discovery payloads, OpenHAB configs) from the schema replaces hand-maintained configuration
- Publish-time payload validation catches shape drift in development before it reaches the broker
- Zero overhead for users who don't enable enforcement — off by default, optional extras, no new topics

### Negative

- Two new optional dependencies (pyyaml, jsonschema) increase the dependency surface for users who enable schema enforcement
- AsyncAPI 3.0.0 Python tooling is immature — the schema loader must be built and maintained in-house rather than using an off-the-shelf parser
- Conditional capability enforcement (tag-based rules) requires custom validation logic that AsyncAPI's native model cannot express — this must be implemented as cosalette-specific code on top of the parsed schema
- Operators who enable schema enforcement must maintain the network schema in sync with their app fleet — a new operational artifact to keep current

_2026-04-09_
