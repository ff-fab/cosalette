# Schema Enforcement Value Analysis — Per-App vs. Network-Centric

**Date:** 2026-04-08
**Context:** Discussion on mqtt-schema-enforcement.md, Question A
**Status:** Deliberation document — not a decision

---

## The Core Question

> If I write the app AND the schema, what am I actually validating? Myself against
> myself?

This is the right question to ask before investing in a 6,000-line architecture. Let's
break it apart honestly.

---

## 1. The Self-Policing Problem

Per-app schema enforcement as currently designed works like this:

```
Developer writes app  ──→  Developer writes schema  ──→  Framework validates app against schema
     (you)                        (you)                         (catches your own mistakes)
```

The value proposition reduces to: **catching drift between what you intended and what
you implemented.** This is real, but bounded:

| Scenario | Per-app schema catches it? | But... |
|----------|---------------------------|--------|
| Typo in payload key (`temp` vs `temperature`) | Yes — UC3 | Only if you wrote the schema first. If you wrote both at the same time, the typo exists in both. |
| Forgot a `/battery` topic for tagged device | Yes — UC1 | You defined the tag. You could have just... remembered. |
| Payload shape drifts over months of edits | Yes — UC3 | Genuine value. Code drifts, schemas don't (if maintained). |
| New developer joins the project | Yes — all UCs | Not your scenario. You are a solo operator. |
| CI catches regression before deploy | Yes — UC3 | Real value, but light. A pytest assertion achieves the same for one-person projects. |

**Honest assessment:** For a single developer running their own apps, per-app schema
enforcement is **nice-to-have, not essential.** The feedback loop is too short — you
already know what each app does because you wrote it. The schema becomes documentation
you're forced to keep in sync, which is a cost as much as a benefit.

---

## 2. Where the Real Value Lives: The Network Level

Your deployment reality:

```
                    ┌─────────────────────────────────────┐
                    │        Smart Home MQTT Broker        │
                    │         (~20 apps, ~6 hosts)         │
                    └────┬────┬────┬────┬────┬────┬───────┘
                         │    │    │    │    │    │
                    vito  air  gas  jee  vel  cal  shelly ...
                    2mqtt  2m  2m   2m   2m   2m   2mqtt
                    ────  ───  ───  ───  ───  ───  ──────
                    Pi-1  Pi-1 Pi-2 Pi-2 Pi-3 Pi-3 Pi-4 ...
```

The problems worth solving are **cross-app**, not within-app:

### 2.1 What topics does my entire system produce?

Today, the answer to "what MQTT topics exist in my smart home?" requires:
- Reading every app's source code
- Or connecting an MQTT explorer and discovering empirically
- Or maintaining a mental model that decays

A **central schema** answers this immediately and machine-readably.

### 2.2 Did I break something by redeploying one app?

You update `vito2mqtt` and accidentally rename a device from `temperature` to
`temp_sensor`. The topic changes from `vito2mqtt/temperature/state` to
`vito2mqtt/temp_sensor/state`. No per-app schema catches this — both the app and its
schema were updated together. But:

- Home Assistant automations that reference `vito2mqtt/temperature/state` break silently
- Grafana dashboards pulling from that topic show stale data
- Other apps that subscribe to `vito2mqtt/temperature/state` get nothing

A **network schema** that says "the system expects `vito2mqtt/temperature/state`" catches
this at deploy time — the app cannot start if it doesn't produce the expected topic.

### 2.3 Is my system healthy right now?

You SSH into a Pi and see it's running. But is it producing the data the rest of the
system expects? With 20 apps across 6 machines:

- Which apps are running?
- Are they producing the expected topics?
- Did a schema drift happen after the last deployment?

A **network compliance monitor** watching `+/schema/status` answers all of these from
a single retained-message dashboard.

### 2.4 Ansible deployment validation

You run `ansible-playbook deploy-fleet.yml`. Before restarting services, Ansible could:

1. Pull the central network schema
2. For each app being deployed: run `cosalette schema check` against the network schema
3. Fail the playbook if any app violates the network contract

This turns deployment into a **validated, gate-checked process** — not "deploy and pray."

---

## 3. Redesigning the Value Chain: Network-First Schema

Instead of each app carrying its own schema, invert the model:

### 3.1 One Central Schema Defines the System

```yaml
# network-schema.yaml — lives in your infrastructure repo, managed by Ansible
asyncapi: 3.0.0
info:
  title: Smart Home MQTT Network
  version: 2.1.0
  description: |
    All expected topics across all cosalette apps.
    Source of truth for the smart home MQTT topology.

x-cosalette-enforcement:
  mode: strict
  network_level: true

channels:
  # --- vito2mqtt channels ---
  vitoTemperature:
    address: vito2mqtt/temperature/state
    x-cosalette-app: vito2mqtt
    x-cosalette-archetype: telemetry
    messages:
      reading:
        payload:
          type: object
          required: [temperature, unit]
          properties:
            temperature: { type: number }
            unit: { type: string, enum: [celsius, fahrenheit] }

  vitoValve:
    address: vito2mqtt/valve/state
    x-cosalette-app: vito2mqtt
    x-cosalette-archetype: device
    messages:
      state:
        payload:
          type: object
          required: [position]
          properties:
            position: { type: integer, minimum: 0, maximum: 100 }

  vitoValveCommand:
    address: vito2mqtt/valve/set
    x-cosalette-app: vito2mqtt
    x-cosalette-archetype: command
    messages:
      command:
        payload:
          type: object
          required: [position]
          properties:
            position: { type: integer, minimum: 0, maximum: 100 }

  # --- airthings2mqtt channels ---
  airthingsAirQuality:
    address: airthings2mqtt/airquality/state
    x-cosalette-app: airthings2mqtt
    x-cosalette-archetype: telemetry
    messages:
      reading:
        payload:
          type: object
          required: [co2, voc, humidity, temperature]
          properties:
            co2: { type: integer, minimum: 0 }
            voc: { type: integer, minimum: 0 }
            humidity: { type: number }
            temperature: { type: number }

  # --- shelly2mqtt channels ---
  shellyRelay1:
    address: shelly2mqtt/relay1/state
    x-cosalette-app: shelly2mqtt
    messages:
      state:
        payload:
          type: object
          required: [on]
          properties:
            on: { type: boolean }

  shellyRelay1Command:
    address: shelly2mqtt/relay1/set
    x-cosalette-app: shelly2mqtt
    messages:
      command:
        payload:
          type: object
          required: [on]
          properties:
            on: { type: boolean }

  # --- Shared rules (all apps) ---
  appStatus:
    address: "{appName}/status"
    x-cosalette-scope: all_apps
    messages:
      status:
        payload:
          type: object
          required: [online]
          properties:
            online: { type: boolean }

  appDiagnostics:
    address: "{appName}/diagnostics"
    x-cosalette-scope: all_apps
    messages:
      diagnostics:
        payload:
          type: object
          required: [uptime_seconds, version, host]
          properties:
            uptime_seconds: { type: integer, minimum: 0 }
            version: { type: string }
            host: { type: string }
```

### 3.2 Each App Validates Against the Relevant Slice

At startup, an app:

1. Loads the network schema (from a path Ansible puts on disk, or from an MQTT retained
   message)
2. Filters to channels where `x-cosalette-app == self.name` (plus `x-cosalette-scope:
   all_apps` shared rules)
3. Validates its registrations against that slice
4. Reports compliance to `{app}/schema/status`

The app does NOT carry its own schema file. The schema is **externally provided** — the
same way pydantic-settings reads environment variables that Ansible configures.

### 3.3 What This Changes About the Architecture

| Aspect | Per-App Schema (current design) | Network-First Schema (proposed pivot) |
|--------|--------------------------------|--------------------------------------|
| Schema authoring | Developer writes per-app schema alongside app code | Operator writes one central schema for the entire system |
| Who validates whom | App validates itself (self-policing) | External authority validates app (separation of concerns) |
| What gets caught | Internal drift within one app | Cross-app contract violations, deployment regressions, topology drift |
| Schema distribution | Each app has its own schema file | One file deployed by Ansible to all hosts (or published as MQTT retained message) |
| Schema evolution | App developer bumps their schema version | Operator bumps network schema version; apps validate on next restart |
| CI integration | App repo runs `cosalette schema check` against its own schema | App repo runs `cosalette schema check` against _network_ schema (pulled from infra repo) |
| Monitoring | Each app knows if it's internally consistent | Network monitor knows if the _system_ is collectively consistent |

---

## 4. Concrete Use Cases That Only Work With a Central Schema

### UC-N1: Deployment Regression Gate

**Scenario:** You refactor `jeelink2mqtt`, renaming the device from `lacrosse` to
`lacrosse_temp`. You push, CI passes (the app works fine). You deploy via Ansible.

**Without network schema:** The topic changes from `jeelink2mqtt/lacrosse/state` to
`jeelink2mqtt/lacrosse_temp/state`. Home Assistant loses the entity. You notice when
the dashboard is blank.

**With network schema:** Ansible runs `cosalette schema check --app jeelink2mqtt:app
--schema /etc/cosalette/network-schema.yaml` before restarting the service. The check
fails:

```text
Schema violation: channel 'jeelinkLacrosse' expects topic
'jeelink2mqtt/lacrosse/state' but no matching registration found.
Registered devices: ['lacrosse_temp']
```

Ansible aborts the deployment. You fix the schema (intentional rename) or the code
(accidental regression) before anything breaks.

### UC-N2: Shared Payload Contracts

**Scenario:** Three apps publish temperature readings. You want ALL of them to use the
same payload shape: `{temperature: number, unit: string}`. Without a central schema,
you enforce this by convention and memory. With a central schema:

```yaml
components:
  schemas:
    StandardTemperature:
      type: object
      required: [temperature, unit]
      properties:
        temperature: { type: number }
        unit: { type: string, enum: [celsius, fahrenheit] }

channels:
  vitoTemperature:
    address: vito2mqtt/temperature/state
    x-cosalette-app: vito2mqtt
    messages:
      reading:
        payload:
          $ref: "#/components/schemas/StandardTemperature"

  airthingsTemperature:
    address: airthings2mqtt/temperature/state
    x-cosalette-app: airthings2mqtt
    messages:
      reading:
        payload:
          $ref: "#/components/schemas/StandardTemperature"

  shellyTemperature:
    address: shelly2mqtt/temperature/state
    x-cosalette-app: shelly2mqtt
    messages:
      reading:
        payload:
          $ref: "#/components/schemas/StandardTemperature"
```

Change `StandardTemperature` once → all three apps are validated against the new shape
on next deploy. No need to update three separate schema files.

### UC-N3: Fleet Health Dashboard

The network schema defines expected channels per app. A monitor subscribes to
`+/schema/status` and cross-references with `+/status` (LWT). One MQTT retained
message gives you:

```json
{
  "total_apps_expected": 20,
  "apps_online": 18,
  "apps_compliant": 17,
  "gaps": [
    "airthings2mqtt: offline (host pi-2 unreachable)",
    "caldates2mqtt: online but missing channel 'diagnostics' (added in schema v2.1)"
  ],
  "evaluated_at": "2026-04-08T16:00:00Z"
}
```

This is a Home Assistant entity that shows the health of the entire system, not just
individual apps.

### UC-N4: Topic Inventory for Automation

You want to write a Home Assistant automation that triggers when ANY cosalette app
reports an error. Instead of hard-coding all 20 `{app}/error` topics, you parse the
network schema:

```python
# automation helper: extract all error topics from network schema
error_topics = [
    ch.address for ch in network_schema.channels.values()
    if ch.address.endswith("/error")
]
# → ["vito2mqtt/error", "airthings2mqtt/error", "shelly2mqtt/error", ...]
```

The schema is the single source of truth for "what topics exist."

### UC-N5: Ansible Pre-Deploy Validation

```yaml
# ansible/roles/cosalette/tasks/deploy.yml
- name: Validate app schema compliance
  command: >
    cosalette schema check
      --app {{ app_module }}:app
      --schema /etc/cosalette/network-schema.yaml
  register: schema_check
  failed_when: schema_check.rc != 0

- name: Restart app service
  systemd:
    name: "{{ app_name }}"
    state: restarted
  when: schema_check.rc == 0
```

This is the Ansible integration that makes the schema actionable in your deployment
pipeline.

---

## 5. What Remains Valuable About Per-App Validation

The network-first model doesn't eliminate per-app validation — it reframes it. Some
per-app checks still carry value:

| Check | Value in per-app context | Value in network context |
|-------|-------------------------|------------------------|
| Payload shape validation at publish time (UC3) | **High** — catches runtime typos before they hit the broker | Same — this is an app-internal concern regardless |
| Capability enforcement (UC1, tags) | **Low** for solo developer — you know your tags | **Higher** if tags are defined in the network schema as required capabilities |
| Mandatory topic enforcement (UC2) | **Low** — framework already wires status/availability | **High** — network schema adds custom mandatory topics (diagnostics, etc.) |
| Schema init / code generation (UC6) | **Medium** — useful as documentation generator | **High** — generates per-app schema _from_ the network schema slice |

**Summary:** Payload validation at publish time stands on its own. Registration
validation becomes much more valuable when the schema is externally provided rather
than self-authored.

---

## 6. Revised Design Direction

### 6.1 Primary Use — Network Schema

The primary schema authored and maintained by the operator (you). Defines:

- **Per-app expected channels:** Which topics each app should produce/consume, keyed
  by `x-cosalette-app`
- **Shared rules:** Channels with `x-cosalette-scope: all_apps` that every app must
  satisfy (e.g., diagnostics endpoint)
- **Shared payload schemas:** `$ref` components reused across apps
  (StandardTemperature, StandardAvailability, etc.)
- **Network-level metadata:** System version, expected hosts, monitoring configuration

### 6.2 Secondary Use — Per-App Schema (Generated, Not Authored)

Instead of manually writing a per-app schema, `cosalette schema init` extracts the
app's slice from the network schema:

```bash
# Extract vito2mqtt's portion of the network schema
cosalette schema slice \
  --network /etc/cosalette/network-schema.yaml \
  --app vito2mqtt \
  --output schema/asyncapi.yaml
```

This produces a standalone AsyncAPI document containing only the channels relevant to
`vito2mqtt`. Useful for:

- Per-app documentation (developer reference)
- Per-app CI validation (without needing the full network schema in the app repo)
- Offline development (work on the app without network schema access)

But the **source of truth remains the network schema**. The per-app slice is a derived
artifact.

### 6.3 What This Means for the Architecture (§4)

Most of the architecture in mqtt-schema-enforcement.md §4 remains valid:

| Component | Status | Change needed? |
|-----------|--------|---------------|
| `_schema.py` data model | **Keep** | Add `x-cosalette-app` and `x-cosalette-scope` to `ChannelSchema` |
| `_schema_loader.py` | **Keep** | Add `filter_for_app(app_name)` method on `SchemaRegistry` |
| Lifecycle integration | **Keep** | Load network schema, filter to app slice, validate |
| `ValidatingMqttPort` | **Keep** | Unchanged — validates payloads against whichever schema is loaded |
| CLI: `schema init` | **Refocus** | Generate from network schema slice, not just registry snapshot |
| CLI: `schema check` | **Refocus** | Primary use: check app against network schema |
| CLI: `schema validate` | **Keep** | Validates any AsyncAPI document (per-app or network) |
| CLI: `schema slice` | **New** | Extract app portion from network schema |
| Network monitor | **Elevate** | From UC7 afterthought to primary monitoring tool |
| Testing strategy | **Keep** | Add network-schema fixtures alongside per-app fixtures |

### 6.4 Distribution Model

```
Infrastructure repo (Ansible-managed)
├── network-schema.yaml          ← single source of truth
├── ansible/
│   ├── roles/cosalette/
│   │   ├── tasks/deploy.yml     ← pre-deploy schema check
│   │   └── files/
│   │       └── network-schema.yaml  ← deployed to /etc/cosalette/
│   └── playbooks/
│       └── deploy-fleet.yml

Each cosalette host
├── /etc/cosalette/
│   └── network-schema.yaml      ← deployed by Ansible
├── /opt/vito2mqtt/
│   └── ...
├── /opt/airthings2mqtt/
│   └── ...
```

Settings integration:

```bash
# Environment variable per app (set by Ansible systemd unit)
COSALETTE_SCHEMA__PATH=/etc/cosalette/network-schema.yaml
COSALETTE_SCHEMA__ENFORCEMENT=strict
```

---

## 7. Open Points for Discussion

### Q1: Does per-app schema enforcement retain standalone value?

**Lean: Yes, but limited.** Payload validation at publish time (UC3) is useful even
without a network schema — it catches runtime errors before they reach the broker.
Registration validation (UC1, UC2) adds little when you write both the app and the
schema. The architecture should support both modes but **optimize for the network
schema case.**

### Q2: Should the network schema also define inter-app data flow?

Example: "vito2mqtt publishes temperature → grafana-exporter subscribes to
vito2mqtt/temperature/state." This would model the dataflow graph, not just the topic
inventory. **Current lean: not yet.** Topic inventory + payload contracts + compliance
monitoring cover the immediate value. Dataflow modeling is a future extension.

### Q3: How does schema evolution work with Ansible fleet management?

Proposed workflow:

1. Edit `network-schema.yaml` in infra repo
2. Commit + PR (schema change is reviewed like code)
3. Ansible deploys updated schema to all hosts
4. Apps validate on next restart (or on MQTT reload signal)
5. Grace period (UC5) allows old payloads during rolling deploy

This is a clean git-tracked workflow that fits your existing Ansible practices.

### Q4: Should we scope "network-first" as Phase V or redesign from Phase I?

Two options:

**Option A — Keep current per-app architecture, add network layer on top.**
Phases I–IV as planned. Phase V adds network schema, `schema slice`, monitor.
Benefit: incremental delivery. Risk: per-app phase might feel low-value during
implementation.

**Option B — Rewrite Phases I–IV with network-first as the primary use case.**
The loader and validator are built around `filter_for_app(app_name)` from day one.
Per-app schema is a passthrough (`filter_for_app` returns the whole document if no
`x-cosalette-app` annotations exist). Benefit: the highest-value use case is primary.
Risk: slightly more complex Phase I.

**Current lean: Option B.** The engineering cost is marginal (one filter method on
`SchemaRegistry`), and it prevents the awkward situation where Phases I–IV deliver
tooling you don't find particularly compelling.

---

## 8. Summary: Where the Value Actually Is

| Value tier | What | Why it matters for your 20-app deployment |
|-----------|------|------------------------------------------|
| **Highest** | Network schema as single source of truth for MQTT topology | You can answer "what topics exist?" without reading 20 apps' source code |
| **High** | Ansible pre-deploy validation against network schema | Catches regressions before they break Home Assistant, Grafana, automations |
| **High** | Network compliance monitor | One dashboard showing the health of all 20 apps collectively |
| **Medium** | Shared payload contracts ($ref components) | Change a temperature schema once, all apps validate against it |
| **Medium** | Publish-time payload validation (per-app, UC3) | Catches runtime typos before they hit the broker — useful regardless of network schema |
| **Low** | Per-app registration validation when you are the sole author | Self-policing — real but bounded value |
| **Low** | Per-app capability enforcement (UC1) when you define the tags | You already know what tags you're using |

The document's current architecture isn't wrong — it's **ordered suboptimally** for your
deployment scenario. The network-level features (UC7) are buried as a final use case
when they should be the headline.
