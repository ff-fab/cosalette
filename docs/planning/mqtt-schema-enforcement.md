# MQTT Schema Enforcement — Network-First Design

**Date:** 2026-04-08
**Epic:** COS-5hx — MQTT Schema Enforcement
**Status:** Planning document — iteration 2 (network-first reorientation)

---

## 1. Problem Statement

cosalette enforces MQTT topic conventions by code (ADR-002) and framework behaviour:
devices automatically publish to `{app}/{device}/state`, subscribe to
`{app}/{device}/set`, and announce availability on `{app}/{device}/availability`.
App-level topics (`{app}/status`, `{app}/error`) are wired by the runtime. The
_structure_ is correct by construction, but five problems remain:

1. **No payload shape enforcement.** A telemetry handler returning
   `{"temp": 22.5}` today and `{"temperature": 22.5}` tomorrow will not be caught —
   downstream consumers silently break.

2. **No capability-based requirements.** There is no way to declare "every device
   tagged `battery_powered` must publish a `/battery` topic with
   `{level: int, charging: bool}`." Capability contracts live only in developer memory.

3. **No machine-readable contract.** Monitoring tools, code generators, and
   documentation systems cannot discover which topics an app produces or consumes —
   only `build_registry_snapshot()` provides runtime introspection, and it carries
   none of the payload schema information.

4. **No validation mode for development.** Typos, missing fields, and type mismatches
   in payloads surface only when an MQTT consumer fails. A dev-time strict mode that
   validates outgoing payloads before publishing would shorten the feedback loop
   dramatically.

5. **No cross-app contract.** Individual apps are internally self-consistent, but the
   _network_ of ~20 cosalette apps across ~6 Raspberry Pi hosts has no collective
   validation. Renaming a device in one app silently breaks Home Assistant automations,
   Grafana dashboards, and other apps that subscribe to the old topic. There is no
   deployment gate, no fleet health view, and no way to answer "what topics does my
   entire system produce?" without reading 20 apps' source code.

This fifth problem is the **primary value driver** for the deployment scenario this
document targets: a solo operator running their own apps across a fleet of Pis,
managed by Ansible. The first four problems remain real — but their value is bounded
when the same person writes both the app and the schema (see §2).

This planning document evaluates schema format options, defines a distribution
strategy, maps use cases to the network-first architecture, and designs concrete
modules for implementation.

---

## 2. Value Analysis — Per-App vs. Network-Centric

### 2.1 The Self-Policing Problem

Per-app schema enforcement as currently designed works like this:

```
Developer writes app  ──→  Developer writes schema  ──→  Framework validates app against schema
     (you)                        (you)                         (catches your own mistakes)
```

The value proposition reduces to: **catching drift between what you intended and what
you implemented.** This is real, but bounded:

| Scenario | Per-app schema catches it? | But... |
|----------|----------------------------|--------|
| Typo in payload key (`temp` vs `temperature`) | Yes — UC6 | Only if you wrote the schema first. If you wrote both at the same time, the typo exists in both. |
| Forgot a `/battery` topic for tagged device | Yes — UC7 | You defined the tag. You could have just... remembered. |
| Payload shape drifts over months of edits | Yes — UC6 | Genuine value. Code drifts, schemas don't (if maintained). |
| New developer joins the project | Yes — all UCs | Not your scenario. You are a solo operator. |
| CI catches regression before deploy | Yes — UC6 | Real value, but light. A pytest assertion achieves the same for one-person projects. |

**Honest assessment:** For a single developer running their own apps, per-app schema
enforcement is **nice-to-have, not essential.** The feedback loop is too short — you
already know what each app does because you wrote it. The schema becomes documentation
you're forced to keep in sync, which is a cost as much as a benefit.

### 2.2 Where the Real Value Lives: The Network Level

The deployment reality:

```
                    ┌─────────────────────────────────────┐
                    │        Smart Home MQTT Broker       │
                    │         (~20 apps, ~6 hosts)        │
                    └────┬────┬────┬────┬────┬────┬───────┘
                         │    │    │    │    │    │
                    vito  air  gas  jee  vel  cal  shelly ...
                    2mqtt  2m  2m   2m   2m   2m   2mqtt
                    ────  ───  ───  ───  ───  ───  ──────
                    Pi-1  Pi-1 Pi-2 Pi-2 Pi-3 Pi-3 Pi-4 ...
```

The problems worth solving are **cross-app**, not within-app:

**What topics does my entire system produce?** Today, the answer requires reading
every app's source code, connecting an MQTT explorer, or maintaining a mental model
that decays. A **central schema** answers this immediately and machine-readably.

**Did I break something by redeploying one app?** You update `vito2mqtt` and
accidentally rename a device from `temperature` to `temp_sensor`. The topic changes
from `vito2mqtt/temperature/state` to `vito2mqtt/temp_sensor/state`. No per-app schema
catches this — both the app and its schema were updated together. But Home Assistant
automations referencing `vito2mqtt/temperature/state` break silently. A **network
schema** catches this at deploy time.

**Is my system collectively healthy?** With 20 apps across 6 machines: which are
running? Which produce the expected topics? Did a schema drift happen after the last
deployment? A **network compliance monitor** answers these from a single retained
message.

**Can deployment be gated on schema compliance?** Ansible runs
`cosalette schema check` against the network schema _before_ restarting services. If
the check fails, the playbook aborts. **Validated, gate-checked deployment** — not
"deploy and pray."

**Can I auto-generate consumer configurations?** The network schema knows every topic,
payload shape, and device metadata across all apps. That's enough to generate Home
Assistant discovery payloads, OpenHAB `.things`/`.items` files, and Grafana dashboard
definitions — from a single source of truth.

### 2.3 Revised Design Direction — Network-First Schema

Instead of each app carrying its own schema, invert the model:

**One central schema defines the system:**

```yaml
# network-schema.yaml — lives in your infrastructure repo, managed by Ansible
asyncapi: 3.0.0
info:
  title: Smart Home MQTT Network
  version: 2.1.0
  description: All expected topics across all cosalette apps.

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
            temperature:
              type: number
              x-cosalette-consumer:
                device_class: temperature
                unit: "°C"
                display_name: "Heating Water Temperature"
                state_class: measurement
            unit:
              type: string
              enum: [celsius, fahrenheit]

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
            co2:
              type: integer
              minimum: 0
              x-cosalette-consumer:
                device_class: carbon_dioxide
                unit: ppm
                display_name: "CO₂"
            voc:
              type: integer
              minimum: 0
              x-cosalette-consumer:
                device_class: volatile_organic_compounds_parts
                unit: ppb
            humidity:
              type: number
              x-cosalette-consumer:
                device_class: humidity
                unit: "%"
            temperature:
              type: number
              x-cosalette-consumer:
                device_class: temperature
                unit: "°C"

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

**Each app validates against its relevant slice.** At startup, an app:

1. Loads the network schema (from a path Ansible puts on disk)
2. Filters to channels where `x-cosalette-app == self.name` (plus
   `x-cosalette-scope: all_apps` shared rules)
3. Validates its registrations against that slice
4. Reports compliance to `{app}/schema/status`

The app does NOT carry its own schema file. The schema is **externally provided** — the
same way pydantic-settings reads environment variables that Ansible configures.

### 2.4 What Changes About the Architecture

| Aspect | Per-App Schema (iteration 1) | Network-First Schema (this iteration) |
|--------|--------------------------------|--------------------------------------|
| Schema authoring | Developer writes per-app schema alongside app code | Operator writes one central schema for the entire system |
| Who validates whom | App validates itself (self-policing) | External authority validates app (separation of concerns) |
| What gets caught | Internal drift within one app | Cross-app contract violations, deployment regressions, topology drift |
| Schema distribution | Each app has its own schema file | One file deployed by Ansible to all hosts |
| Schema evolution | App developer bumps their schema version | Operator bumps network schema version; apps validate on next restart |
| CI integration | App repo validates against its own schema | App repo validates against _network_ schema (pulled from infra repo) |
| Consumer generation | Not possible — no cross-app view | HA discovery, OpenHAB configs generated from the network schema |

### 2.5 Value Tier Summary

| Value Tier | Capability | Why It Matters for ~20-App Deployment |
|-----------|------|------------------------------------------|
| **Highest** | Network schema as single source of truth for MQTT topology | Answer "what topics exist?" without reading 20 apps' source code |
| **Highest** | Ansible pre-deploy validation against network schema | Catch regressions before they break HA, Grafana, automations |
| **High** | Consumer code generation (HA discovery, OpenHAB) | Auto-generate configs from the schema instead of hand-maintaining them |
| **High** | Network compliance monitor | One dashboard showing the health of all 20 apps collectively |
| **Medium** | Shared payload contracts (`$ref` components) | Change a temperature schema once, all apps validate against it |
| **Medium** | Publish-time payload validation (per-app, UC6) | Catches runtime typos before they hit the broker |
| **Low** | Per-app registration validation when sole author | Self-policing — real but bounded value |
| **Low** | Per-app capability enforcement when you define the tags | You already know what tags you're using |

### 2.6 What Remains Valuable About Per-App Validation

The network-first model doesn't eliminate per-app validation — it reframes it:

| Check | Per-App Value | Network Value |
|-------|--------------|---------------|
| Payload shape validation at publish time | **High** — catches runtime typos before they hit the broker | Same — app-internal concern regardless |
| Capability enforcement (tags) | **Low** for solo dev | **Higher** if tags are defined in the network schema |
| Mandatory topic enforcement | **Low** — framework already wires status/availability | **High** — network schema adds custom mandatory topics |
| Schema init / code generation | **Medium** — documentation generator | **High** — generates per-app schema _from_ the network schema slice |

**Summary:** Payload validation at publish time stands on its own. Registration
validation becomes much more valuable when the schema is externally provided rather
than self-authored.

---

## 3. Schema Format Evaluation (Summary)

> **Full evaluation:** See Appendix A for the complete AsyncAPI deep-dive, alternative
> format analysis, and worked examples.

### 3.1 Candidates Evaluated

| Option | Format | Description |
|--------|--------|-------------|
| **AsyncAPI 3.0.0** | Industry standard | Channel/operation model with JSON Schema payloads, MQTT bindings, `x-` extensions |
| **Option A** | JSON Schema + Custom Manifest | Standalone JSON Schema files for payloads, lightweight YAML manifest for topology |
| **Option B** | HA Discovery Format | Home Assistant MQTT discovery payload structure |
| **Option C** | Custom YAML DSL | Purpose-built cosalette format with native concepts |

### 3.2 Weighted Scoring Matrix

| # | Dimension | Weight | AsyncAPI | Option A | Option B | Option C |
|---|-----------|--------|:---:|:---:|:---:|:---:|
| 1 | Expressiveness | ×3 | 4 | 4 | 1 | 5 |
| 2 | Payload validation | ×3 | 5 | 5 | 1 | 5 |
| 3 | Capability enforcement | ×3 | 2 | 4 | 1 | 5 |
| 4 | Tooling ecosystem | ×2 | 4 | 3 | 3 | 1 |
| 5 | Runtime footprint | ×2 | 3 | 4 | 5 | 5 |
| 6 | Learning curve | ×1 | 2 | 3 | 4 | 5 |
| 7 | Interoperability | ×1 | 5 | 2 | 3 | 1 |
| 8 | Evolvability | ×2 | 3 | 4 | 1 | 5 |
| 9 | Distribution flexibility | ×2 | 4 | 4 | 2 | 4 |

| Option | Total |
|--------|-------|
| AsyncAPI 3.0.0 | **68** |
| Option A (JSON Schema + Manifest) | **74** |
| Option B (HA Discovery) | **38** |
| Option C (Custom YAML DSL) | **81** |

### 3.3 Recommendation

**Primary format: AsyncAPI 3.0.0 with `x-cosalette-*` extensions (hybrid approach).**

Option C scores highest on raw points, but carries the strategic risk of reinventing
AsyncAPI badly — as schema enforcement matures, a custom format accumulates features
that AsyncAPI already specifies (`$ref`, bindings, traits, server definitions). Option B
is eliminated (score 38) — it's an output target, not a source schema.

The hybrid approach layers cosalette semantics onto AsyncAPI's structural foundation:

1. **AsyncAPI 3.0.0 is the canonical document format.** Standard channels, operations,
   MQTT bindings, and JSON Schema payloads.
2. **`x-cosalette-*` extensions carry cosalette semantics.** Purpose-built metadata
   that AsyncAPI doesn't model natively.
3. **Custom tooling extracts and validates extensions.** `pyyaml` + `jsonschema` —
   no immature AsyncAPI Python library required.

**Extension properties (iteration 2 additions marked with ★):**

| Extension | Placement | Purpose |
|-----------|-----------|---------|
| `x-cosalette-enforcement` | Document root | Enforcement mode (`strict`/`warn`/`off`) and lifecycle hooks |
| `x-cosalette-archetype` | Operation/channel | Maps to cosalette archetype: `telemetry`, `command`, `device` (ADR-010) |
| `x-cosalette-requires` | Channel | Tag-based capability requirement |
| `x-cosalette-coalescing-group` | Operation | Coalescing group membership (ADR-018) |
| ★ `x-cosalette-app` | Channel | Identifies which app owns a channel (network schema) |
| ★ `x-cosalette-scope` | Channel | `all_apps` = every app must satisfy this channel |
| ★ `x-cosalette-consumer` | Property | Generic consumer metadata: `device_class`, `unit`, `display_name`, `icon`, `state_class`, `read_only` |
| ★ `x-cosalette-ha-discovery` | Property/channel | HA-specific overrides: `component`, `value_template`, `command_template` |
| ★ `x-cosalette-openhab` | Property/channel | OpenHAB-specific overrides: `item_type`, `label`, `groups`, `tags` |

**Rationale:**

1. Industry-standard structure — community tooling works on the standard portion.
2. Lightweight extension for cosalette semantics — not a parallel specification.
3. Proven validation path: `pyyaml` → `$ref` resolution → `jsonschema`.
4. Documentation for free — AsyncAPI Studio, Redoc renderers, Spectral linting.
5. Strategic alignment — if schemas are published externally, AsyncAPI is immediately
   legible; `x-cosalette-*` degrades gracefully.

**Pivot triggers to Option C:** Extension properties exceed 40% of document key count;
AsyncAPI 4.x breaks `x-` extension compatibility; developer feedback consistently cites
AsyncAPI overhead.

**Fallback path:** Migration cost is bounded — JSON Schema payloads are portable,
`x-cosalette-*` keys map directly to Option C's native keys.

---

## 4. Schema Distribution

The network schema must reach ~20 apps on ~6 hosts. This section evaluates distribution
mechanisms and recommends a phased approach.

### 4.1 Distribution Mechanisms

#### 4.1.1 Ansible File Deployment

Deploy `network-schema.yaml` to `/etc/cosalette/` on each host via Ansible role:

```yaml
# ansible/roles/cosalette_schema/tasks/main.yml
- name: Deploy network schema
  ansible.builtin.copy:
    src: network-schema.yaml
    dest: /etc/cosalette/network-schema.yaml
    owner: root
    group: cosalette
    mode: "0644"
  notify: validate and restart cosalette apps

- name: Validate schema compliance for each app
  ansible.builtin.command: >
    cosalette schema check
      --app {{ item.module }}:app
      --schema /etc/cosalette/network-schema.yaml
  loop: "{{ cosalette_apps }}"
  register: schema_checks
  failed_when: schema_checks.rc != 0

- name: Restart cosalette apps
  ansible.builtin.systemd:
    name: "{{ item.service }}"
    state: restarted
  loop: "{{ cosalette_apps }}"
  when: schema_checks is succeeded
```

| Aspect | Assessment |
|--------|-----------|
| **Offline resilience** | Excellent — file is local, no network dependency at runtime |
| **Update latency** | Slow — requires playbook run + service restart |
| **Versioning** | Excellent — schema is in git, Ansible tracks deployments |
| **Ansible integration** | Native — this IS the Ansible workflow |
| **Pre-deploy validation** | Perfect — `cosalette schema check` runs before restart |
| **Size constraints** | None — filesystem has no size limit |
| **Complexity** | Minimal — standard Ansible file deployment pattern |

#### 4.1.2 MQTT Retained Message

Publish the full schema as a retained message on `cosalette/schema/network`:

```text
Topic:   cosalette/schema/network
QoS:     1
Retain:  true
Payload: <raw YAML bytes>
```

| Aspect | Assessment |
|--------|-----------|
| **Offline resilience** | Poor — depends on broker availability at app startup |
| **Update latency** | Instant — subscribers receive immediately |
| **Versioning** | Poor — no history, only current version on broker |
| **Ansible integration** | Weak — requires `mosquitto_pub` or MQTT client in playbook |
| **Pre-deploy validation** | Poor — cannot gate deployment on MQTT message content |
| **Size constraints** | Problematic — Mosquitto default max is 256 KB |
| **Complexity** | Medium — YAML serialization over MQTT, size monitoring |

#### 4.1.3 Git Submodule

Schema repository as a git submodule in each app repo:

| Aspect | Assessment |
|--------|-----------|
| **Offline resilience** | Medium — needs git pull, but submodule is local after clone |
| **Update latency** | Slow — requires `git submodule update` + rebuild |
| **Versioning** | Excellent — full git history |
| **Ansible integration** | Medium — submodule update adds complexity to deploy |
| **Pre-deploy validation** | Good — schema available at build/deploy time |
| **Size constraints** | None |
| **Complexity** | High — git submodules are notoriously error-prone |

#### 4.1.4 HTTP Endpoint

Serve schema from an HTTP server (e.g., `http://pi-1:8080/schema/network.yaml`):

| Aspect | Assessment |
|--------|-----------|
| **Offline resilience** | Poor — requires HTTP server running and reachable |
| **Update latency** | Medium — apps must poll or be told to re-fetch |
| **Versioning** | Good — versioned URLs (`/v2/schema.yaml`) |
| **Ansible integration** | Weak — separate infrastructure to manage |
| **Pre-deploy validation** | Medium — can curl and validate, but extra step |
| **Size constraints** | None |
| **Complexity** | High — requires running an HTTP server, TLS, availability |

#### 4.1.5 Ansible + MQTT Hybrid

File deployment as primary, MQTT **reload signal** as supplement. Ansible deploys the
file AND publishes a lightweight reload trigger (NOT the full schema):

```yaml
# Deploy file (primary)
- name: Deploy network schema
  ansible.builtin.copy:
    src: network-schema.yaml
    dest: /etc/cosalette/network-schema.yaml

# Validate before restart
- name: Validate schema compliance
  ansible.builtin.command: >
    cosalette schema check
      --app {{ item.module }}:app
      --schema /etc/cosalette/network-schema.yaml
  loop: "{{ cosalette_apps }}"

# Signal running apps to reload (optional — avoids restart)
- name: Publish reload signal
  ansible.builtin.command: >
    mosquitto_pub -t cosalette/schema/update -m '{"schema_version": "2.1.0", "issued_at": "2026-04-09T12:00:00Z", "request_id": "deploy-2026-04-09-120000", "issuer": "ansible"}'
  when: schema_checks is succeeded
```

Running apps receive the signal on `cosalette/schema/update` and re-read the
**local file** — the MQTT message carries only a version hint, not the schema itself.

> **Authorization boundary (resolved in planning):** Control topics use the MQTT
> broker as the trust boundary, with **unique principals and narrow ACLs**.
>
> - Only the deployment principal may publish `cosalette/schema/update`.
> - Each app principal may publish only its own `{app}/schema/status`.
> - Only the network-monitor principal may publish
>   `cosalette/network/schema/status`.
> - App principals subscribe to `cosalette/schema/update` but may not publish there.
>
> Signed control messages are **not required in v1** because the MQTT payload is only a
> reload hint; the authoritative schema remains the local file deployed by Ansible.
>
> **Decision note:** See `docs/planning/schema-control-topic-authorization.md`
> (`COS-cjg`).

| Aspect | Assessment |
|--------|-----------|
| **Offline resilience** | Excellent — file is local; MQTT signal is supplementary |
| **Update latency** | Good — reload signal avoids full restart |
| **Versioning** | Excellent — file is in git |
| **Ansible integration** | Good — file deploy is native, `mosquitto_pub` adds one step |
| **Pre-deploy validation** | Perfect — same as pure Ansible |
| **Size constraints** | None — signal is tiny, schema is on disk |
| **Complexity** | Medium — adds MQTT signal subscription to the framework |

### 4.2 Decision Matrix

| # | Criterion | Weight | Ansible File | MQTT Retained | Git Submodule | HTTP Endpoint | Hybrid |
|---|-----------|--------|:---:|:---:|:---:|:---:|:---:|
| 1 | Offline resilience | ×3 | 5 | 2 | 3 | 2 | 5 |
| 2 | Update latency (running apps) | ×2 | 2 | 5 | 1 | 3 | 4 |
| 3 | Versioning / rollback | ×3 | 5 | 1 | 5 | 4 | 5 |
| 4 | Ansible integration | ×3 | 5 | 2 | 3 | 2 | 4 |
| 5 | Size constraints | ×1 | 5 | 2 | 5 | 5 | 4 |
| 6 | Complexity | ×2 | 5 | 3 | 2 | 3 | 3 |
| 7 | Pre-deploy validation | ×3 | 5 | 1 | 4 | 3 | 5 |

**Weighted totals:**

| Mechanism | Calculation | Total |
|-----------|-------------|-------|
| **Ansible File** | (5×3)+(2×2)+(5×3)+(5×3)+(5×1)+(5×2)+(5×3) = 15+4+15+15+5+10+15 | **79** |
| **MQTT Retained** | (2×3)+(5×2)+(1×3)+(2×3)+(2×1)+(3×2)+(1×3) = 6+10+3+6+2+6+3 | **36** |
| **Git Submodule** | (3×3)+(1×2)+(5×3)+(3×3)+(5×1)+(2×2)+(4×3) = 9+2+15+9+5+4+12 | **56** |
| **HTTP Endpoint** | (2×3)+(3×2)+(4×3)+(2×3)+(5×1)+(3×2)+(3×3) = 6+6+12+6+5+6+9 | **50** |
| **Hybrid** | (5×3)+(4×2)+(5×3)+(4×3)+(4×1)+(3×2)+(5×3) = 15+8+15+12+4+6+15 | **75** |

### 4.3 Recommendation

**Primary (Phase 1): Pure Ansible file deployment** (score: 79).

The Ansible file approach scores highest because it aligns perfectly with the existing
deployment workflow: schema is a file in the infrastructure repo, deployed by Ansible,
validated before restart. No new infrastructure, no broker dependencies, no size limits.

**Phase 2 enhancement: Hybrid — add MQTT reload signal** (score: 75).

Once the file-based flow is proven, add optional MQTT reload signaling to avoid
full service restarts when only the schema changes. The signal is a lightweight message
(`{"version": "2.1.0"}`) on `cosalette/schema/update` that tells running apps to
re-read their local schema file.

#### Phase 1: File Layout

```
Infrastructure repo (Ansible-managed)
├── network-schema.yaml          ← single source of truth
├── ansible/
│   ├── roles/cosalette_schema/
│   │   └── tasks/main.yml       ← deploy + validate + restart
│   └── playbooks/
│       └── deploy-fleet.yml
```

Each cosalette host after deployment:

```
/etc/cosalette/
└── network-schema.yaml          ← deployed by Ansible
```

#### Settings Integration

```bash
# Environment variable per app (set by Ansible in systemd unit)
COSALETTE_SCHEMA__PATH=/etc/cosalette/network-schema.yaml
COSALETTE_SCHEMA__ENFORCEMENT=strict
```

Systemd unit file (deployed by Ansible):

```ini
# /etc/systemd/system/vito2mqtt.service
[Unit]
Description=vito2mqtt cosalette app
After=mosquitto.service

[Service]
Type=simple
User=cosalette
Environment=COSALETTE_SCHEMA__PATH=/etc/cosalette/network-schema.yaml
Environment=COSALETTE_SCHEMA__ENFORCEMENT=strict
ExecStart=/opt/vito2mqtt/.venv/bin/python -m vito2mqtt
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

#### Phase 2: Reload Signal

```python
# Framework-internal subscription (added when schema enforcement is active)
async def _on_schema_broadcast(self, payload: bytes) -> None:
    """Handle fleet-wide schema update broadcast."""
    msg = orjson.loads(payload)
    new_version = msg.get("version")
    if new_version and new_version != self._schema_registry.version:
        await self._schema_lifecycle.reload()
```

The reload re-reads the **local file** (not the MQTT message), re-validates
registrations, and publishes updated compliance status. This provides instant
propagation without the size limits and offline-resilience concerns of MQTT-carried
schemas.

#### ADR Input for Distribution Decision

This analysis is ready for an Architecture Decision Record:

- **Decision:** Adopt Ansible file deployment as the primary schema distribution
  mechanism, with optional MQTT reload signaling as a Phase 2 enhancement.
- **Context:** Five mechanisms evaluated. Ansible file scores highest (79/85) on a
  7-dimension weighted matrix prioritizing offline resilience, Ansible integration,
  and pre-deploy validation.
- **Consequences:** Schema file at `/etc/cosalette/network-schema.yaml`. Settings via
  `COSALETTE_SCHEMA__PATH`. No new infrastructure required. Phase 2 adds
  `cosalette/schema/update` MQTT topic for reload signaling.

---

## 5. Use Cases

Use cases are ordered by value tier (§2.5), with network-level use cases first.

### UC1: Ansible Pre-Deploy Validation (★ Highest Value)

#### Problem

You run `ansible-playbook deploy-fleet.yml`. The playbook copies new app code to Pi-2,
restarts `jeelink2mqtt`. The device was renamed from `lacrosse` to `lacrosse_temp` in a
refactor. The topic changes from `jeelink2mqtt/lacrosse/state` to
`jeelink2mqtt/lacrosse_temp/state`. Home Assistant loses the entity. You notice when the
dashboard is blank — hours or days later.

There is no deployment gate. Ansible deploys and restarts unconditionally.

#### Solution: Schema Check as Ansible Gate

```yaml
# ansible/roles/cosalette/tasks/deploy.yml
- name: Copy app code
  ansible.builtin.synchronize:
    src: "apps/{{ app_name }}/"
    dest: "/opt/{{ app_name }}/"
  notify: validate and restart

# Handler: validate before restart
- name: Validate app against network schema
  ansible.builtin.command: >
    /opt/{{ app_name }}/.venv/bin/cosalette schema check
      --app {{ app_module }}:app
      --schema /etc/cosalette/network-schema.yaml
  register: schema_check
  failed_when: schema_check.rc != 0

- name: Restart app service (only if validation passed)
  ansible.builtin.systemd:
    name: "{{ app_name }}"
    state: restarted
  when: schema_check.rc == 0
```

#### How `cosalette schema check` Works

The CLI command loads the app module, builds the registry snapshot, filters the network
schema to the app's slice, and compares:

```text
$ cosalette schema check --app jeelink2mqtt:app --schema /etc/cosalette/network-schema.yaml

Schema: /etc/cosalette/network-schema.yaml (v2.1.0)
App:    jeelink2mqtt

✗ jeelinkLacrosse — MISSING
    Schema expects topic 'jeelink2mqtt/lacrosse/state'
    but no matching registration found.
    Registered devices: ['lacrosse_temp']

⚠ lacrosseTempState — EXTRA
    App registers 'jeelink2mqtt/lacrosse_temp/state'
    but schema has no matching channel.

Result: 1 missing, 1 extra, 0 compliant
Exit code: 1
```

Ansible sees exit code 1, fails the task, and never restarts the service. The old
version keeps running with the old (correct) topic. The operator sees the error, decides
whether to update the schema (intentional rename) or fix the code (accidental
regression).

#### Ansible Playbook: Full Fleet Deploy

```yaml
# ansible/playbooks/deploy-fleet.yml
---
- name: Deploy cosalette fleet
  hosts: cosalette_hosts
  become: true

  vars:
    schema_path: /etc/cosalette/network-schema.yaml

  tasks:
    - name: Deploy network schema
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/../network-schema.yaml"
        dest: "{{ schema_path }}"
        owner: root
        group: cosalette
        mode: "0644"

    - name: Deploy and validate each app
      ansible.builtin.include_tasks: deploy-app.yml
      loop: "{{ cosalette_apps | selectattr('host', 'eq', inventory_hostname) }}"
      loop_control:
        loop_var: app

# ansible/tasks/deploy-app.yml
---
- name: "{{ app.name }} — sync code"
  ansible.builtin.synchronize:
    src: "apps/{{ app.name }}/"
    dest: "/opt/{{ app.name }}/"

- name: "{{ app.name }} — validate schema compliance"
  ansible.builtin.command: >
    /opt/{{ app.name }}/.venv/bin/cosalette schema check
      --app {{ app.module }}:app
      --schema {{ schema_path }}
  register: check_result
  changed_when: false
  failed_when: check_result.rc != 0

- name: "{{ app.name }} — restart service"
  ansible.builtin.systemd:
    name: "{{ app.name }}"
    state: restarted
```

#### Value

This is the highest-value use case because it **prevents production breakage at the
point of deployment**. It turns Ansible from "copy and restart" into "copy, validate,
restart only if valid." For a fleet of 20 apps where a single rename can break HA
automations, Grafana dashboards, and inter-app subscriptions, this gate pays for itself
on the first caught regression.

---

### UC2: Network Compliance Monitoring (★ High Value)

#### Problem

20 apps run across 6 hosts. Are they all producing the expected topics? Did a schema
drift happen after the last deployment? Today, answering this requires SSH-ing into each
host and checking manually.

#### Solution: Fleet Health via `+/schema/status`

Each app publishes its compliance status as a retained message:

```json
// vito2mqtt/schema/status (retained)
{
  "app": "vito2mqtt",
  "schema_version": "2.1.0",
  "compliant": true,
  "channels": [
    {"name": "vitoTemperature", "status": "compliant"},
    {"name": "vitoValve", "status": "compliant"}
  ],
  "reported_at": "2026-04-08T16:00:00Z"
}
```

A network monitor subscribes to `+/schema/status`, cross-references with `+/status`
(LWT), and publishes an aggregate:

```json
// cosalette/network/schema/status (retained, MQTT 5 message expiry recommended)
{
  "total_apps_expected": 20,
  "apps_online": 18,
  "apps_compliant": 17,
  "gaps": [
    "airthings2mqtt: offline (host pi-2 unreachable)",
    "caldates2mqtt: online but missing channel 'diagnostics' (added in schema v2.1)"
  ],
  "evaluated_at": "2026-04-08T16:00:00Z",
  "expires_after_seconds": 300
}
```

**Freshness policy:** Retained schema-status messages should use MQTT 5 message expiry
(recommended: 5 minutes) so stale data does not persist indefinitely. Consumers should
treat retained status older than `expires_after_seconds` as unknown/non-compliant. The
network monitor should also publish an LWT so its offline state is visible to dashboards.

This becomes a Home Assistant entity showing the health of the entire system — not just
individual apps.

#### Framework Integration

Schema status publishing is automatic when enforcement is active — no app code needed.
The framework publishes after `on_configure` validation and after each schema reload:

```python
async def _publish_schema_status(self) -> None:
    """Publish the app's schema compliance status (automatic)."""
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
        retain=True, qos=1,
    )
```

---

### UC3: Consumer Code Generation — Home Assistant Discovery (★ High Value, New)

#### Problem

Home Assistant MQTT integration requires publishing discovery payloads to
`homeassistant/<component>/<node_id>/<object_id>/config` for each entity. Today, there
is **zero HA discovery implementation** in cosalette (confirmed by codebase search).
Topic structure aligns with HA conventions, but no discovery payload is generated.

Manually writing discovery payloads for ~20 apps with dozens of entities is tedious
and error-prone. The network schema already contains everything needed: topic addresses,
payload shapes, and (with `x-cosalette-consumer`) device metadata.

#### Solution: Generate HA Discovery from Network Schema

The `x-cosalette-consumer` extension on payload properties provides the metadata HA
discovery needs:

```yaml
# In network-schema.yaml
channels:
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
            temperature:
              type: number
              x-cosalette-consumer:
                device_class: temperature
                unit: "°C"
                display_name: "Heating Water Temperature"
                state_class: measurement
                icon: mdi:thermometer
              x-cosalette-ha-discovery:
                value_template: "{{ value_json.temperature }}"
```

Generated HA discovery payload:

```json
// Published to: homeassistant/sensor/vito2mqtt/temperature_temperature/config
{
  "name": "Heating Water Temperature",
  "state_topic": "vito2mqtt/temperature/state",
  "value_template": "{{ value_json.temperature }}",
  "device_class": "temperature",
  "unit_of_measurement": "°C",
  "state_class": "measurement",
  "icon": "mdi:thermometer",
  "unique_id": "cosalette_vito2mqtt_temperature_temperature",
  "device": {
    "identifiers": ["cosalette_vito2mqtt_temperature"],
    "name": "vito2mqtt temperature",
    "manufacturer": "cosalette",
    "model": "vito2mqtt"
  },
  "availability": [
    {
      "topic": "vito2mqtt/temperature/availability",
      "payload_available": "online",
      "payload_not_available": "offline"
    }
  ]
}
```

#### CLI Command

```text
$ cosalette ha-discovery generate --schema /etc/cosalette/network-schema.yaml --app vito2mqtt

Generated 8 HA discovery payloads for vito2mqtt:
  sensor/vito2mqtt/temperature_temperature/config
  sensor/vito2mqtt/temperature_unit/config
  sensor/vito2mqtt/valve_position/config
  ...

$ cosalette ha-discovery publish --schema /etc/cosalette/network-schema.yaml --app vito2mqtt
Published 8 discovery payloads to homeassistant/...
```

Two modes:

1. **`generate`** — outputs JSON to stdout for inspection and copy-paste.
2. **`publish`** — connects to the broker and publishes as retained messages.

#### HA Component Mapping

The generator maps JSON Schema types + `x-cosalette-consumer` metadata to HA components:

| JSON Schema Type | `device_class` | HA Component |
|-----------------|----------------|-------------|
| `number` | temperature, humidity, etc. | `sensor` |
| `boolean` | (none) | `binary_sensor` |
| `boolean` + command channel | (none) | `switch` |
| `integer` + min/max | (none) | `number` |
| `string` + enum | (none) | `select` |

For complex devices (e.g., climate), the `x-cosalette-ha-discovery` extension allows
explicit component override:

```yaml
x-cosalette-ha-discovery:
  component: climate
  temperature_command_topic: vito2mqtt/valve/set
  temperature_state_template: "{{ value_json.temperature }}"
  modes: ["heat", "off"]
```

#### Implementation Relationship to §6

The full gap analysis for HA discovery support is in §6. This use case describes the
_workflow_ (generate from schema → publish to HA); §6 details the _gaps_ in
cosalette's current implementation that must be filled.

---

### UC4: Consumer Code Generation — OpenHAB (★ High Value, New)

#### Problem

OpenHAB's MQTT binding requires `.things` and `.items` configuration files that
describe MQTT channels, their types, state/command topics, and transformations. Like
HA discovery, this is manual work that duplicates information already in the network
schema.

#### Solution: Generate `.things` and `.items` from Network Schema

The `x-cosalette-openhab` extension provides OpenHAB-specific overrides:

```yaml
# In network-schema.yaml
channels:
  vitoTemperature:
    address: vito2mqtt/temperature/state
    x-cosalette-app: vito2mqtt
    messages:
      reading:
        payload:
          type: object
          properties:
            temperature:
              type: number
              x-cosalette-consumer:
                device_class: temperature
                unit: "°C"
                display_name: "Heating Water Temperature"
              x-cosalette-openhab:
                item_type: "Number:Temperature"
                label: "Heating Water [%.1f °C]"
                groups: ["gTemperature", "gVito"]
                tags: ["Measurement", "Temperature"]
```

Generated `.things` file:

```java
// vito2mqtt.things — auto-generated from network-schema.yaml v2.1.0
Thing mqtt:topic:mosquitto:vito2mqtt "vito2mqtt" (mqtt:broker:mosquitto) {
    Channels:
        Type number : temperature_temperature "Heating Water Temperature" [
            stateTopic="vito2mqtt/temperature/state",
            transformationPattern="JSONPATH:$.temperature"
        ]
        Type number : temperature_unit "Temperature Unit" [
            stateTopic="vito2mqtt/temperature/state",
            transformationPattern="JSONPATH:$.unit"
        ]
}
```

Generated `.items` file:

```java
// vito2mqtt.items — auto-generated from network-schema.yaml v2.1.0
Number:Temperature Vito2mqtt_Temperature_Temperature
    "Heating Water [%.1f °C]"
    <temperature>
    (gTemperature, gVito)
    ["Measurement", "Temperature"]
    { channel="mqtt:topic:mosquitto:vito2mqtt:temperature_temperature" }
```

#### CLI Commands

```text
$ cosalette openhab things --schema /etc/cosalette/network-schema.yaml --app vito2mqtt
# Outputs .things file to stdout

$ cosalette openhab items --schema /etc/cosalette/network-schema.yaml --app vito2mqtt
# Outputs .items file to stdout
```

#### Type Mapping (JSON Schema → OpenHAB)

| JSON Schema | `x-cosalette-consumer.unit` | OpenHAB Item Type |
|-------------|---------------------------|-------------------|
| `number` | Temperature unit (°C, °F) | `Number:Temperature` |
| `number` | Humidity unit (%) | `Number:Dimensionless` |
| `number` | Pressure unit (hPa) | `Number:Pressure` |
| `number` | (other/none) | `Number` |
| `integer` | ppm, ppb | `Number:Dimensionless` |
| `integer` + min/max | (none) | `Dimmer` or `Number` |
| `boolean` | — | `Switch` |
| `string` | — | `String` |
| `string` + enum | — | `String` |

The `x-cosalette-openhab.item_type` override takes precedence over auto-detection.

#### Deployment Integration

Generated files can be:
1. Piped to files and committed to the OpenHAB config repo
2. Deployed by Ansible alongside the schema
3. Used as reference for manual configuration

```yaml
# ansible/roles/openhab/tasks/generate.yml
- name: Generate OpenHAB things files
  ansible.builtin.command: >
    cosalette openhab things
      --schema /etc/cosalette/network-schema.yaml
      --app {{ item }}
  loop: "{{ cosalette_apps | map(attribute='name') }}"
  register: things_output

- name: Write things files
  ansible.builtin.copy:
    content: "{{ item.stdout }}"
    dest: "/etc/openhab/things/{{ item.item }}.things"
  loop: "{{ things_output.results }}"
```

---

### UC5: Shared Payload Contracts (★ Medium Value, New)

#### Problem

Three apps publish temperature readings. Each uses a slightly different payload shape:
`{"temperature": 22.5, "unit": "celsius"}` vs `{"temp": 22.5}` vs
`{"value": 22.5, "unit": "C"}`. Consumers must handle all variants. There is no
mechanism to enforce a standard shape.

#### Solution: `$ref` Components in Network Schema

```yaml
components:
  schemas:
    StandardTemperature:
      type: object
      required: [temperature, unit]
      properties:
        temperature:
          type: number
          x-cosalette-consumer:
            device_class: temperature
            state_class: measurement
        unit:
          type: string
          enum: [celsius, fahrenheit]

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

Change `StandardTemperature` once → all three apps validate against the new shape on
next deploy. Consumer code generators (UC3, UC4) use the shared schema to produce
consistent HA/OpenHAB configurations.

---

### UC6: Payload Shape Validation (Carried from Iteration 1)

#### Problem

A telemetry handler returns `{"temp": 22.5}` instead of the expected
`{"temperature": 22.5, "unit": "celsius"}`. The payload is published without validation.
Downstream consumers that expect the documented shape receive malformed data.

#### Solution: Publish-Time Validation

The `ValidatingMqttPort` wrapper intercepts every `publish()` call and validates the
payload dict against the schema:

- **`strict` mode:** Suppresses publish, sends structured error to `{app}/error`.
- **`warn` mode:** Logs warning, publishes the payload anyway.
- **`off` mode:** Zero overhead — wrapper not instantiated.

```text
$ uv run python -m vito2mqtt
cosalette.PayloadValidationError: payload for 'vito2mqtt/temperature/state'
failed schema validation:

  • 'temperature' is a required property
  • Additional properties are not allowed ('temp' was unexpected)

  Payload:   {"temp": 22.5}
  Expected:  {"temperature": <number>, "unit": "celsius"|"fahrenheit"}
```

In warn mode (production), the payload is published with a WARNING log. This use case
stands on its own regardless of network vs per-app schema — it catches runtime errors
before they reach the broker.

Full architecture in §7.4 (Payload Validation at Publish Time).

---

### UC7: Device Capability Enforcement (Carried from Iteration 1)

#### Problem

A cosalette app registers a device with the tag `battery_powered`, but the developer
forgets to include a `/battery` topic handler. The omission is invisible until a
downstream consumer fails to find battery data.

#### Solution: Tag-Based Channel Requirements

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
            level: { type: integer, minimum: 0, maximum: 100 }
            charging: { type: boolean }
```

At startup (`on_configure`), the framework checks: every device with tag
`battery_powered` must have a registration matching this channel. Missing matches
produce a `SchemaViolation`.

**Value note:** This use case has **low standalone value** for a solo operator (you
defined the tags), but **higher value in the network-first model** where the
network schema defines capability requirements that individual app developers must
satisfy.

---

### UC8: Mandatory Topics (Carried from Iteration 1)

#### Problem

ADR-002 specifies that every app publishes `{app}/status` and every device has
`{app}/{device}/availability`. The framework wires these automatically. But a
deployment policy might require _additional_ mandatory topics beyond framework
defaults — e.g., `{app}/diagnostics` with `{uptime_seconds, version}`.

#### Solution: `x-cosalette-scope: all_apps` Channels

In the network schema:

```yaml
channels:
  appDiagnostics:
    address: "{appName}/diagnostics"
    x-cosalette-scope: all_apps
    messages:
      diagnostics:
        payload:
          type: object
          required: [uptime_seconds, version]
          properties:
            uptime_seconds: { type: integer, minimum: 0 }
            version: { type: string }
```

Every app validates against this channel. App-level mandatory topics are expressed
via `x-cosalette-scope: all_apps`; per-device mandatory topics use the existing
`{deviceName}` parameter in the address template.

---

### UC9: Schema Migration and Grace Periods (Carried from Iteration 1)

#### Problem

Schema v2.0 adds a mandatory `diagnostics` topic. If deployed fleet-wide at 14:00
and three apps have not been updated, they all fail validation simultaneously.

#### Solution: Temporal Grace Periods

```yaml
x-cosalette-enforcement:
  mode: strict
  migration:
    previous_version: "1.0.0"
    additions:
      - channel: appDiagnostics
        required_from: "2026-07-01"
        grace_period: "14d"
        description: "All apps must publish diagnostics topic"
```

| Phase | Condition | Behaviour |
|-------|-----------|-----------|
| `warning` | Date < `required_from` | Log info; validation passes |
| `soft_failure` | Date ≥ `required_from`, within grace | Log error; validation passes |
| `hard_failure` | Date > `required_from` + grace | Validation fails in strict mode |

This is particularly important for the network-first model: when the operator adds a
new mandatory channel, apps need time to implement it. Grace periods provide a smooth
migration window without fleet-wide breakage.

---

### UC10: Developer Tooling (Updated from Iteration 1)

The CLI surface for the network-first model includes both original commands and new
additions:

| Command | Purpose |
|---------|---------|
| `cosalette schema validate <path>` | Static validation of AsyncAPI document |
| `cosalette schema check --app <m:a> --schema <path>` | Registration validation (CI gate) |
| `cosalette schema dump --app <m:a>` | Generate AsyncAPI from app's registry snapshot |
| `cosalette schema init --app <m:a>` | Generate starter schema from registry |
| ★ `cosalette schema slice --network <path> --app <name>` | Extract app's portion from network schema |
| ★ `cosalette schema acl --schema <path> [--broker <name>] [--deploy-user <name>] [--monitor-user <name>]` | Generate broker-specific ACL config from schema (app usernames derived from schema) |
| ★ `cosalette ha-discovery generate --schema <path> --app <name>` | Generate HA discovery payloads |
| ★ `cosalette ha-discovery publish --schema <path> --app <name>` | Publish HA discovery to broker |
| ★ `cosalette openhab things --schema <path> --app <name>` | Generate OpenHAB .things file |
| ★ `cosalette openhab items --schema <path> --app <name>` | Generate OpenHAB .items file |

#### `cosalette schema slice` — Extract App Portion

Instead of manually writing a per-app schema, extract the app's portion from the
network schema:

```text
$ cosalette schema slice --network /etc/cosalette/network-schema.yaml --app vito2mqtt

asyncapi: 3.0.0
info:
  title: vito2mqtt (slice of Smart Home MQTT Network v2.1.0)
  version: 2.1.0
channels:
  vitoTemperature:
    address: vito2mqtt/temperature/state
    ...
  vitoValve:
    address: vito2mqtt/valve/state
    ...
  appStatus:
    address: vito2mqtt/status
    ...
```

This produces a standalone AsyncAPI document for per-app documentation, CI validation
without the full network schema, and offline development.

#### CI Integration

```yaml
# .github/workflows/ci.yml (per-app repo)
- name: Schema gate
  run: |
    # Pull network schema from infra repo
    curl -o network-schema.yaml https://raw.githubusercontent.com/org/infra/main/network-schema.yaml
    uv run cosalette schema check --app myapp:app --schema network-schema.yaml
```

#### Taskfile Integration

```yaml
# Taskfile.yml additions
schema:validate:
  desc: Validate the AsyncAPI schema document
  cmds: ["uv run cosalette schema validate {{.CLI_ARGS}}"]

schema:check:
  desc: Dry-run schema registration validation (CI gate)
  cmds: ["uv run cosalette schema check {{.CLI_ARGS}}"]

schema:slice:
  desc: Extract app portion from network schema
  cmds: ["uv run cosalette schema slice {{.CLI_ARGS}}"]

schema:acl:
  desc: Generate broker-specific ACL config from network schema
  cmds: ["uv run cosalette schema acl {{.CLI_ARGS}}"]

ha-discovery:generate:
  desc: Generate HA discovery payloads from schema
  cmds: ["uv run cosalette ha-discovery generate {{.CLI_ARGS}}"]

openhab:things:
  desc: Generate OpenHAB .things from schema
  cmds: ["uv run cosalette openhab things {{.CLI_ARGS}}"]
```

---

### UC11: Schema Distribution and Update (Updated from Iteration 1)

This use case is addressed by §4 (Schema Distribution). The recommended approach is:

- **Phase 1:** Ansible file deployment to `/etc/cosalette/network-schema.yaml`
- **Phase 2:** Add MQTT reload signal on `cosalette/schema/update`

Apps load the schema from the local file path configured via
`COSALETTE_SCHEMA__PATH`. Hot-reload is optional — the primary update path is
Ansible deploy + service restart. See §4 for full analysis.

---

## 6. Home Assistant Discovery — Gap Analysis

cosalette's topic structure aligns with Home Assistant conventions (ADR-002), but
**no HA discovery implementation exists** (confirmed by codebase search). This section
documents the full gap between cosalette's current state and complete HA MQTT discovery
support, and proposes the metadata additions needed.

### 6.1 Current State

| What cosalette has | What HA discovery needs | Gap |
|---|---|---|
| Topic structure: `{app}/{device}/state` | State topic ✓ | None — topics are compatible |
| Topic structure: `{app}/{device}/set` | Command topic ✓ | None |
| Topic structure: `{app}/{device}/availability` | Availability topic ✓ | None |
| LWT: `{app}/status` with `online`/`offline` | App-level availability ✓ | None |
| Device registration: name, type | `device.name`, `device.model` (partial) | Partial — model from `app_name`, manufacturer missing |
| **Nothing** | `device_class` (temperature, humidity, etc.) | **Full gap** |
| **Nothing** | `unit_of_measurement` (°C, %, ppm) | **Full gap** |
| **Nothing** | `state_class` (measurement, total, total_increasing) | **Full gap** |
| **Nothing** | `icon` (mdi:thermometer, etc.) | **Full gap** |
| **Nothing** | `value_template` (Jinja2 extraction) | **Full gap** |
| **Nothing** | `command_template` (Jinja2 formatting) | **Full gap** |
| **Nothing** | `unique_id` (stable entity identifier) | **Full gap** |
| **Nothing** | `friendly_name` / display name | **Full gap** |
| **Nothing** | Discovery topic publication | **Full gap** |
| **Nothing** | Discovery payload generation | **Full gap** |

**Summary:** cosalette has compatible topic structure but **none** of the metadata or
publishing machinery needed for HA discovery.

### 6.2 HA Discovery Payload Format by Component

#### sensor

```json
{
  "name": "Heating Water Temperature",
  "state_topic": "vito2mqtt/temperature/state",
  "value_template": "{{ value_json.temperature }}",
  "device_class": "temperature",
  "unit_of_measurement": "°C",
  "state_class": "measurement",
  "icon": "mdi:thermometer",
  "unique_id": "cosalette_vito2mqtt_temperature_temperature",
  "device": {
    "identifiers": ["cosalette_vito2mqtt_temperature"],
    "name": "vito2mqtt temperature",
    "manufacturer": "cosalette",
    "model": "vito2mqtt"
  },
  "availability": [
    {
      "topic": "vito2mqtt/temperature/availability",
      "payload_available": "online",
      "payload_not_available": "offline"
    }
  ]
}
```

Published to: `homeassistant/sensor/vito2mqtt/temperature_temperature/config`

#### binary_sensor

```json
{
  "name": "Motion Detected",
  "state_topic": "shelly2mqtt/motion/state",
  "value_template": "{{ value_json.detected }}",
  "device_class": "motion",
  "payload_on": true,
  "payload_off": false,
  "unique_id": "cosalette_shelly2mqtt_motion_detected",
  "device": { "identifiers": ["cosalette_shelly2mqtt_motion"], "name": "shelly2mqtt motion" },
  "availability": [{"topic": "shelly2mqtt/motion/availability"}]
}
```

Published to: `homeassistant/binary_sensor/shelly2mqtt/motion_detected/config`

#### switch

```json
{
  "name": "Relay 1",
  "state_topic": "shelly2mqtt/relay1/state",
  "command_topic": "shelly2mqtt/relay1/set",
  "value_template": "{{ value_json.on }}",
  "command_template": "{\"on\": {{ value }} }",
  "payload_on": "true",
  "payload_off": "false",
  "device_class": "switch",
  "unique_id": "cosalette_shelly2mqtt_relay1",
  "device": { "identifiers": ["cosalette_shelly2mqtt_relay1"], "name": "shelly2mqtt relay1" },
  "availability": [{"topic": "shelly2mqtt/relay1/availability"}]
}
```

Published to: `homeassistant/switch/shelly2mqtt/relay1/config`

#### number

```json
{
  "name": "Valve Position",
  "state_topic": "vito2mqtt/valve/state",
  "command_topic": "vito2mqtt/valve/set",
  "value_template": "{{ value_json.position }}",
  "command_template": "{\"position\": {{ value }} }",
  "min": 0,
  "max": 100,
  "step": 1,
  "unit_of_measurement": "%",
  "unique_id": "cosalette_vito2mqtt_valve_position",
  "device": { "identifiers": ["cosalette_vito2mqtt_valve"], "name": "vito2mqtt valve" },
  "availability": [{"topic": "vito2mqtt/valve/availability"}]
}
```

Published to: `homeassistant/number/vito2mqtt/valve_position/config`

#### climate

```json
{
  "name": "Heating Circuit",
  "current_temperature_topic": "vito2mqtt/temperature/state",
  "current_temperature_template": "{{ value_json.temperature }}",
  "temperature_command_topic": "vito2mqtt/valve/set",
  "temperature_state_topic": "vito2mqtt/valve/state",
  "temperature_state_template": "{{ value_json.position }}",
  "modes": ["heat", "off"],
  "unique_id": "cosalette_vito2mqtt_climate",
  "device": { "identifiers": ["cosalette_vito2mqtt"], "name": "vito2mqtt" },
  "availability": [{"topic": "vito2mqtt/status"}]
}
```

Published to: `homeassistant/climate/vito2mqtt/heating_circuit/config`

### 6.3 Metadata Gaps in cosalette Registration

The current registration dataclasses in `_registration.py` have **no fields** for HA
discovery metadata:

| Field Needed | `_TelemetryRegistration` | `_DeviceRegistration` | `_CommandRegistration` |
|---|:---:|:---:|:---:|
| `device_class` | ✗ | ✗ | ✗ |
| `unit_of_measurement` | ✗ | ✗ | ✗ |
| `state_class` | ✗ | ✗ | ✗ |
| `icon` | ✗ | ✗ | ✗ |
| `friendly_name` | ✗ | ✗ | ✗ |
| `value_template` | ✗ | ✗ | ✗ |
| `command_template` | ✗ | — | ✗ |
| `min` / `max` / `step` | ✗ | ✗ | ✗ |

### 6.4 Two Approaches to Filling the Gap

#### Approach A: Decorator-Level Metadata

Add optional metadata parameters to cosalette decorators:

```python
@app.telemetry(
    "temperature",
    interval=60,
    device_class="temperature",
    unit="°C",
    display_name="Heating Water Temperature",
    icon="mdi:thermometer",
    state_class="measurement",
)
async def read_temperature(ctx: DeviceContext) -> dict:
    return {"temperature": await sensor.read(), "unit": "celsius"}
```

**Pros:** Metadata lives next to the code. Per-app schema not required.
**Cons:** Verbose decorators. Metadata is scattered per-app, not centralized.

#### Approach B: Schema Extension Metadata

Define metadata in the network schema via `x-cosalette-consumer`:

```yaml
properties:
  temperature:
    type: number
    x-cosalette-consumer:
      device_class: temperature
      unit: "°C"
      display_name: "Heating Water Temperature"
      icon: mdi:thermometer
      state_class: measurement
```

**Pros:** Centralized in one file. Generates both HA and OpenHAB configs. Schema is
the single source of truth.
**Cons:** Schema must be authored/maintained. Metadata is separate from code.

#### Recommendation: Approach B (Schema Extensions) as Primary

For the network-first model, **Approach B is clearly superior**:

1. The network schema already exists as the source of truth.
2. Consumer metadata belongs in the consumer-facing schema, not buried in app code.
3. One schema generates both HA discovery AND OpenHAB configs.
4. The operator (who knows what HA/OpenHAB expects) authors the metadata, not the
   app developer (who knows the sensor).

Approach A remains available as a **convenience layer** for apps that don't use the
network schema — the decorator metadata can be used to auto-generate a per-app schema
or to supplement the network schema with defaults.

### 6.5 Unified Consumer Extension: `x-cosalette-consumer`

A single extension carries metadata useful for **all** consumer platforms (HA, OpenHAB,
Grafana, etc.):

```yaml
x-cosalette-consumer:
  device_class: temperature       # HA device_class, OpenHAB semantic tag
  unit: "°C"                      # HA unit_of_measurement, OpenHAB unit
  display_name: "Heating Water"   # HA name, OpenHAB label
  icon: mdi:thermometer           # HA icon, OpenHAB icon
  state_class: measurement        # HA state_class (measurement|total|total_increasing)
  read_only: true                 # No command channel (informational)
```

Platform-specific overrides layer on top:

```yaml
x-cosalette-ha-discovery:
  component: sensor               # Explicit HA component (auto-detected if omitted)
  value_template: "{{ value_json.temperature | round(1) }}"
  expire_after: 600               # HA-specific: entity goes unavailable after N seconds

x-cosalette-openhab:
  item_type: "Number:Temperature"  # Explicit OpenHAB type
  label: "Heating Water [%.1f °C]" # OpenHAB label with format
  groups: ["gTemperature"]         # OpenHAB group membership
  tags: ["Measurement"]            # OpenHAB semantic tags
```

### 6.6 Implementation Effort Estimate

| Gap | Effort | Phase |
|-----|--------|-------|
| Define `x-cosalette-consumer` schema | 0.5 days | Schema extensions |
| Define `x-cosalette-ha-discovery` schema | 0.5 days | Schema extensions |
| Define `x-cosalette-openhab` schema | 0.5 days | Schema extensions |
| HA discovery payload generator | 2–3 days | Code generation module |
| HA discovery CLI (`ha-discovery generate/publish`) | 1 day | CLI |
| OpenHAB `.things`/`.items` generator | 2–3 days | Code generation module |
| OpenHAB CLI (`openhab things/items`) | 1 day | CLI |
| Extension parsing in schema loader | 1 day | Loader update |
| Test coverage for generators | 2 days | Testing |
| **Total** | **~10–12 days** | |

This is **not** in the critical path for the core schema enforcement feature (§7–§8).
Consumer code generation is a **follow-on phase** that builds on the schema
infrastructure.

### 6.7 Network Schema vs. Registry Snapshot as Generation Source

Two possible sources for generating consumer configs:

| Source | Pros | Cons |
|--------|------|------|
| Network schema | Has payload field info, consumer metadata, cross-app view | Must be authored upfront |
| Registry snapshot (`build_registry_snapshot()`) | Available automatically | No payload field info, no device_class/unit/icon |

**Recommendation: Network schema.** The registry snapshot carries device names and
archetype info but has zero payload structure information — `_TelemetryRegistration`
stores the handler function, not the return schema. Only the network schema (or
Approach A decorator metadata) has the field-level detail needed for
`value_template` generation.

---

## 7. Architecture Design

This section updates the architecture from iteration 1 to support the network-first
model. The core modules (`_schema.py`, `_schema_loader.py`, `ValidatingMqttPort`) are
largely unchanged — the network-first model adds **filtering** and **consumer code
generation** on top of the same data model.

### 7.1 Schema Module Design (`_schema.py`)

The pure data-model module for parsed AsyncAPI 3.0.0 + `x-cosalette-*`. Contains
frozen dataclasses and a `PayloadValidator` that pre-compiles JSON Schema validators.

**Design principles:**

- **No I/O.** Loading/resolving is the loader's job (§7.2).
- **Immutable after construction.** All `frozen=True` dataclasses.
- **Thin layer over AsyncAPI.** Field names align with AsyncAPI terminology.

#### Core Data Model (Updated for Network-First)

```python
@dataclass(frozen=True, slots=True)
class EnforcementConfig:
    """Document-level enforcement settings from ``x-cosalette-enforcement``."""
    mode: Literal["strict", "warn", "off"] = "warn"
    on_configure: bool = True
    on_publish: bool = False
    network_level: bool = False  # ★ NEW: marks this as a network schema
```

> **Resolved (2026-04-09): Default enforcement mode is `off`, not `warn`.**
>
> The default for `mode` changes from `"warn"` to `"off"`. Rationale:
>
> - `off` is the zero-friction default — users who don't enable schema enforcement
>   have no operational burden (no new topics, no ACL requirements, no new dependencies
>   loaded).
> - Matches the existing framework philosophy: zero-config defaults that don't
>   surprise.
> - Users opt into enforcement explicitly via `COSALETTE_SCHEMA__MODE=warn` or
>   `=strict`.
> - Aligns with the operational posture documented in
>   `docs/planning/schema-control-topic-authorization.md` §8.1.
>
> Update the dataclass default to `"off"` at implementation time.

```python
@dataclass(frozen=True, slots=True)
class ConsumerMetadata:
    """Generic consumer metadata from ``x-cosalette-consumer``. ★ NEW"""
    device_class: str | None = None
    unit: str | None = None
    display_name: str | None = None
    icon: str | None = None
    state_class: str | None = None
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class HaDiscoveryOverrides:
    """HA-specific overrides from ``x-cosalette-ha-discovery``. ★ NEW"""
    component: str | None = None
    value_template: str | None = None
    command_template: str | None = None
    expire_after: int | None = None


@dataclass(frozen=True, slots=True)
class OpenHabOverrides:
    """OpenHAB-specific overrides from ``x-cosalette-openhab``. ★ NEW"""
    item_type: str | None = None
    label: str | None = None
    groups: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PropertySchema:
    """A single property in a payload schema, with consumer metadata. ★ NEW"""
    name: str
    json_schema: dict[str, Any]
    consumer: ConsumerMetadata | None = None
    ha_discovery: HaDiscoveryOverrides | None = None
    openhab: OpenHabOverrides | None = None


@dataclass(frozen=True, slots=True)
class ChannelSchema:
    """Parsed representation of a single AsyncAPI channel."""
    address: str
    address_template: str
    direction: Literal["send", "receive", "both"]
    payload_schema: dict[str, Any] | None = None
    mqtt_binding: MqttBinding = field(default_factory=MqttBinding)
    capability_requirements: list[CapabilityRequirement] = field(default_factory=list)
    archetype: Literal["telemetry", "command", "device"] | None = None
    coalescing_group: str | None = None
    message_name: str | None = None
    app_name: str | None = None       # ★ NEW: from x-cosalette-app
    scope: str | None = None           # ★ NEW: from x-cosalette-scope
    properties: dict[str, PropertySchema] = field(default_factory=dict)  # ★ NEW


@dataclass(frozen=True, slots=True)
class SchemaRegistry:
    """Top-level container for a parsed schema."""
    app_name: str | None  # None for network schemas
    app_version: str
    asyncapi_version: str
    enforcement: EnforcementConfig
    channels: dict[str, ChannelSchema]
    operations: dict[str, OperationSchema]
    component_schemas: dict[str, dict[str, Any]]
    device_names: frozenset[str]

    # ★ NEW: Network-first query helpers

    def filter_for_app(self, app_name: str) -> SchemaRegistry:
        """Return a new registry containing only channels for *app_name*.

        Includes channels where:
        - ``x-cosalette-app == app_name``
        - ``x-cosalette-scope == "all_apps"``

        Used by apps loading a network schema to get their relevant slice.
        """
        filtered = {
            name: ch for name, ch in self.channels.items()
            if ch.app_name == app_name or ch.scope == "all_apps"
        }
        return SchemaRegistry(
            app_name=app_name,
            app_version=self.app_version,
            asyncapi_version=self.asyncapi_version,
            enforcement=self.enforcement,
            channels=filtered,
            operations={
                name: op for name, op in self.operations.items()
                if op.channel_ref in filtered
            },
            component_schemas=self.component_schemas,
            device_names=self._extract_device_names(filtered),
        )

    def all_app_names(self) -> frozenset[str]:
        """Return all unique app names referenced in the schema."""
        return frozenset(
            ch.app_name for ch in self.channels.values()
            if ch.app_name is not None
        )

    def channels_for_device(self, device_name: str) -> list[ChannelSchema]:
        """Return all channels whose address contains *device_name*."""
        # ... (unchanged from iteration 1)

    def required_channels_for_tag(self, tag: str) -> list[ChannelSchema]:
        """Return channels requiring *tag* via ``x-cosalette-requires``."""
        # ... (unchanged from iteration 1)

    def payload_schema_for_topic(self, topic: str) -> dict[str, Any] | None:
        """Look up the JSON Schema for a resolved topic."""
        # ... (unchanged from iteration 1)
```

The key addition is `filter_for_app()` — a single method that transforms a network
schema into an app-specific slice. If no `x-cosalette-app` annotations exist (pure
per-app schema), `filter_for_app()` returns the whole document unchanged.

#### PayloadValidator

Unchanged from iteration 1. Pre-compiles `jsonschema.Draft7Validator` instances at
construction time. See Appendix A for the full implementation.

### 7.2 Schema Loader Design (`_schema_loader.py`)

The I/O and parsing module. Loads AsyncAPI YAML, resolves `$ref` pointers, validates
`x-cosalette-*` extensions, returns a `SchemaRegistry`.

**Updated for network-first: additional extension extraction.**

The loader now extracts:

- `x-cosalette-app` → `ChannelSchema.app_name`
- `x-cosalette-scope` → `ChannelSchema.scope`
- `x-cosalette-consumer` → `PropertySchema.consumer`
- `x-cosalette-ha-discovery` → `PropertySchema.ha_discovery`
- `x-cosalette-openhab` → `PropertySchema.openhab`

#### Network Schema Loading Pipeline

```python
async def load_network_schema(
    source: SchemaSource,
    app_name: str,
) -> SchemaRegistry:
    """Load a network schema and filter to a specific app's slice.

    1. Load full network schema via load_schema()
    2. Filter to channels for app_name + all_apps scope
    3. Return the filtered SchemaRegistry
    """
    full_registry = await load_schema(source)
    return full_registry.filter_for_app(app_name)
```

The `SchemaSource` protocol and concrete implementations (`FileSchemaSource`,
`MqttSchemaSource`, `InlineSchemaSource`) are unchanged from iteration 1. The
`HttpSchemaSource` is deferred — not needed for Phase 1 (Ansible file deployment).

### 7.3 Lifecycle Integration

The insertion point in `App._run_async()` is unchanged:

```
resolve_settings() → configure_logging() → resolve_adapters() →
run_configure_hooks() → expand_name_specs() → resolve_intervals() →
★ load_and_validate_schema() →
create_mqtt() → ...
```

**Updated `load_and_validate_schema()` for network-first:**

```python
async def load_and_validate_schema(
    app: App,
    settings: Settings,
    prefix: str,
) -> SchemaRegistry | None:
    """Load the schema document and validate app registrations."""
    if settings.schema_.enforcement == "off":
        return None

    path = _resolve_schema_path(settings)
    if path is None:
        return None

    source = FileSchemaSource(path)
    full_registry = await load_schema(source)

    # ★ NEW: Network-first filtering
    if full_registry.enforcement.network_level:
        registry = full_registry.filter_for_app(app.name)
    else:
        registry = full_registry

    violations = _validate_registrations(app, registry, prefix)

    if violations and settings.schema_.enforcement == "strict":
        raise SchemaViolationError(violations)
    elif violations:
        for v in violations:
            logger.warning("Schema violation: %s", v.message)

    return registry
```

The key difference: when `network_level: true` is set, the registry is filtered to the
app's slice before validation. The rest of the enforcement pipeline is identical.

### 7.4 Payload Validation at Publish Time

`ValidatingMqttPort` is unchanged from iteration 1. It wraps `MqttPort`, intercepts
`publish()`, validates dict payloads against pre-compiled JSON Schema validators, and
handles violations per enforcement mode.

See iteration 1 §4.4 for full design: `ValidatingMqttPort` class, performance
considerations, error flow, skip-topic mechanism, and lifecycle wiring.

### 7.5 Consumer Code Generation Module (★ New)

A new module `cosalette/_consumer_gen.py` generates consumer configurations from the
network schema.

#### Architecture

```
network-schema.yaml
        │
        ▼
  SchemaRegistry (with PropertySchema + consumer metadata)
        │
        ├──→ HaDiscoveryGenerator → HA discovery JSON payloads
        │         │
        │         ├── generate() → list[DiscoveryPayload]
        │         └── publish(mqtt) → publishes to homeassistant/...
        │
        └──→ OpenHabGenerator → .things / .items content
                  │
                  ├── generate_things() → str
                  └── generate_items() → str
```

#### HA Discovery Generator

```python
@dataclass(frozen=True)
class DiscoveryPayload:
    """A single HA MQTT discovery payload."""
    component: str          # sensor, binary_sensor, switch, number, climate
    node_id: str            # app name (e.g., vito2mqtt)
    object_id: str          # unique within the node (e.g., temperature_temperature)
    config: dict[str, Any]  # the full discovery JSON payload


class HaDiscoveryGenerator:
    """Generate HA MQTT discovery payloads from a SchemaRegistry."""

    def __init__(self, registry: SchemaRegistry) -> None:
        self._registry = registry

    def generate(self) -> list[DiscoveryPayload]:
        """Generate discovery payloads for all channels with consumer metadata."""
        payloads: list[DiscoveryPayload] = []
        for ch_name, channel in self._registry.channels.items():
            for prop_name, prop in channel.properties.items():
                if prop.consumer is None:
                    continue
                payload = self._build_discovery_payload(channel, prop_name, prop)
                if payload is not None:
                    payloads.append(payload)
        return payloads

    def _build_discovery_payload(
        self,
        channel: ChannelSchema,
        prop_name: str,
        prop: PropertySchema,
    ) -> DiscoveryPayload | None:
        """Build a single discovery payload for a property."""
        consumer = prop.consumer
        ha = prop.ha_discovery

        component = self._detect_component(channel, prop)
        if component is None:
            return None

        app_name = channel.app_name or "unknown"
        device_name = self._extract_device_name(channel.address)
        object_id = f"{device_name}_{prop_name}"

        config: dict[str, Any] = {
            "name": consumer.display_name or f"{device_name} {prop_name}",
            "state_topic": channel.address,
            "unique_id": f"cosalette_{app_name}_{object_id}",
            "device": {
                "identifiers": [f"cosalette_{app_name}_{device_name}"],
                "name": f"{app_name} {device_name}",
                "manufacturer": "cosalette",
                "model": app_name,
            },
        }

        # Consumer metadata
        if consumer.device_class:
            config["device_class"] = consumer.device_class
        if consumer.unit:
            config["unit_of_measurement"] = consumer.unit
        if consumer.state_class:
            config["state_class"] = consumer.state_class
        if consumer.icon:
            config["icon"] = consumer.icon

        # Value template
        if ha and ha.value_template:
            config["value_template"] = ha.value_template
        else:
            config["value_template"] = f"{{{{ value_json.{prop_name} }}}}"

        # Command topic (if channel has a matching command channel)
        command_channel = self._find_command_channel(channel)
        if command_channel:
            config["command_topic"] = command_channel.address

        # Availability
        avail_topic = channel.address.rsplit("/", 1)[0] + "/availability"
        config["availability"] = [{
            "topic": avail_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
        }]

        return DiscoveryPayload(
            component=component,
            node_id=app_name,
            object_id=object_id,
            config=config,
        )
```

#### OpenHAB Generator

```python
class OpenHabGenerator:
    """Generate OpenHAB .things and .items from a SchemaRegistry."""

    def __init__(self, registry: SchemaRegistry, broker_name: str = "mosquitto") -> None:
        self._registry = registry
        self._broker = broker_name

    def generate_things(self) -> str:
        """Generate .things file content."""
        app_name = self._registry.app_name or "unknown"
        lines = [
            f'// {app_name}.things — auto-generated from network schema v{self._registry.app_version}',
            f'Thing mqtt:topic:{self._broker}:{app_name} "{app_name}" (mqtt:broker:{self._broker}) {{',
            '    Channels:',
        ]
        for ch_name, channel in self._registry.channels.items():
            for prop_name, prop in channel.properties.items():
                oh = prop.openhab
                consumer = prop.consumer
                channel_id = f"{self._extract_device(channel)}_{prop_name}"
                label = (consumer.display_name or prop_name) if consumer else prop_name
                oh_type = self._detect_type(prop)

                lines.append(
                    f'        Type {oh_type} : {channel_id} "{label}" ['
                )
                lines.append(
                    f'            stateTopic="{channel.address}",'
                )
                lines.append(
                    f'            transformationPattern="JSONPATH:$.{prop_name}"'
                )
                lines.append('        ]')
        lines.append('}')
        return '\n'.join(lines)

    def generate_items(self) -> str:
        """Generate .items file content."""
        app_name = self._registry.app_name or "unknown"
        lines = [f'// {app_name}.items — auto-generated from network schema v{self._registry.app_version}']
        for ch_name, channel in self._registry.channels.items():
            for prop_name, prop in channel.properties.items():
                oh = prop.openhab
                consumer = prop.consumer
                item_type = oh.item_type if oh else self._detect_item_type(prop)
                item_name = self._item_name(app_name, channel, prop_name)
                label = oh.label if oh else (f'"{consumer.display_name}"' if consumer and consumer.display_name else f'"{prop_name}"')
                groups = f'({", ".join(oh.groups)})' if oh and oh.groups else ''
                tags = f'[{", ".join(f\'"{t}"\' for t in oh.tags)}]' if oh and oh.tags else ''
                channel_id = f"mqtt:topic:{self._broker}:{app_name}:{self._extract_device(channel)}_{prop_name}"

                lines.append(
                    f'{item_type} {item_name} {label} {groups} {tags} {{ channel="{channel_id}" }}'
                )
        return '\n'.join(lines)
```

### 7.6 CLI Tooling Architecture

ADR-005 chose Typer as the CLI framework. The schema CLI is the first consumer.

**Module:** `cosalette/_cli.py`

**Entry point:** `cosalette` console script via `pyproject.toml`:

```toml
[project.scripts]
cosalette = "cosalette._cli:app"
```

**Command groups:**

```python
app = typer.Typer(name="cosalette", no_args_is_help=True)
schema_app = typer.Typer(name="schema", help="AsyncAPI schema management.")
ha_app = typer.Typer(name="ha-discovery", help="Home Assistant discovery.")
oh_app = typer.Typer(name="openhab", help="OpenHAB configuration generation.")

app.add_typer(schema_app, name="schema")
app.add_typer(ha_app, name="ha-discovery")
app.add_typer(oh_app, name="openhab")
```

**Full command reference:** See UC10 (§5) for all commands and their purpose.

### 7.7 Testing Strategy

The testing strategy follows ADR-007 and extends iteration 1's test plan.

#### New Test Areas for Network-First

| Test Area | Key Tests |
|-----------|-----------|
| `SchemaRegistry.filter_for_app()` | Filters by `x-cosalette-app`, includes `all_apps` scope, excludes other apps |
| `ConsumerMetadata` extraction | Parser extracts `x-cosalette-consumer` from properties |
| `HaDiscoveryGenerator` | Generates correct payloads for sensor, binary_sensor, switch, number |
| `OpenHabGenerator` | Generates valid .things and .items for all property types |
| Network schema lifecycle | App loads network schema, filters to slice, validates |
| `schema slice` CLI | Produces valid standalone AsyncAPI for one app |
| `ha-discovery generate` CLI | Outputs valid JSON discovery payloads |
| `openhab things` CLI | Outputs valid .things syntax |

#### Test Fixtures

```
tests/fixtures/schemas/
├── valid_basic.yaml                 # Minimal per-app schema
├── valid_full.yaml                  # Per-app with all extensions
├── network_basic.yaml               # ★ NEW: Minimal network schema (2 apps)
├── network_full.yaml                # ★ NEW: Network schema with consumer metadata
├── network_ha_discovery.yaml        # ★ NEW: Network schema with HA discovery extensions
├── network_openhab.yaml             # ★ NEW: Network schema with OpenHAB extensions
├── invalid_version.yaml             # asyncapi: 2.6.0
├── invalid_refs.yaml                # Unresolvable $ref
├── circular_refs.yaml               # Circular $ref chain
├── invalid_extensions.yaml          # Malformed x-cosalette-*
└── payloads/                        # Sample JSON payloads
```

#### Coverage Targets

| Module | Target |
|--------|--------|
| `_schema.py` (including `filter_for_app`) | 95% |
| `_schema_loader.py` (including consumer extraction) | 90% |
| `PayloadValidator` | 95% |
| `ValidatingMqttPort` | 90% |
| `_consumer_gen.py` (HA + OpenHAB generators) | 90% |
| `_cli.py` | 85% |
| Lifecycle integration | 85% |

### 7.8 Dependency Analysis

#### Runtime Dependencies

| Package | Version | Purpose | Size |
|---------|---------|---------|------|
| `pyyaml` | ≥6.0 | Parse AsyncAPI YAML | ~200 KB |
| `jsonschema` | ≥4.20 | Validate payloads against JSON Schema | ~400 KB (+transitives) |

Both are **new** — neither pulled in transitively by existing deps. Gated behind
optional extra:

```toml
[project.optional-dependencies]
schema = ["pyyaml>=6.0", "jsonschema>=4.20"]
```

Total new runtime footprint: ~1.7 MB (including `jsonschema` transitives:
`referencing`, `attrs`, `rpds-py`, `jsonschema-specifications`).

#### Optional Dependencies

| Package | Purpose | When Needed |
|---------|---------|-------------|
| `ruamel.yaml` ≥0.18 | Round-trip YAML editing | `schema init` CLI (comment preservation) |

#### Test Dependencies

| Package | Purpose |
|---------|---------|
| `hypothesis-jsonschema` | Generate valid/invalid payloads for property-based testing |

---

## 8. Implementation Roadmap

### 8.1 Phased Delivery

Phases are reordered for the network-first model. Phases I–III deliver the core schema
infrastructure. Phase IV adds consumer code generation. Phase V adds network monitoring.

#### Phase I — Core Schema Data Model and Loader (2–3 days)

| Attribute | Detail |
|-----------|--------|
| **Scope** | `_schema.py` + `_schema_loader.py` |
| **Key additions** | `filter_for_app()`, `ConsumerMetadata`, `PropertySchema` |
| **Dependencies** | `pyyaml`, `jsonschema` as `cosalette[schema]` optional extra |

**Deliverables:**
- `SchemaRegistry`, `ChannelSchema`, `PropertySchema`, `ConsumerMetadata`,
  `HaDiscoveryOverrides`, `OpenHabOverrides` dataclasses
- `FileSchemaSource` + `InlineSchemaSource`
- `$ref` resolution, `x-cosalette-*` extraction (including new extensions)
- `filter_for_app()` method

> **Resolved (2026-04-09): Multi-file `$ref` support is out of scope for Phase I;
> single-file schemas only.**
>
> - Phase I targets a single-file network schema, which is sufficient for the ~20 app
>   fleet.
> - External `$ref` resolution adds parser complexity and file-discovery logic.
> - If schemas grow large enough to warrant splitting, this can be added as a
>   non-breaking enhancement (the schema loader gains a resolver; existing single-file
>   schemas continue to work).
> - Tracked as a potential Phase VII extension.

**Acceptance:** Can load the network schema example from §2.3, filter to `vito2mqtt`,
and return a `SchemaRegistry` with the correct channels and consumer metadata.

#### Phase II — Lifecycle Integration and Registration Validation (2–3 days)

| Attribute | Detail |
|-----------|--------|
| **Scope** | `_wiring.py`, `_settings.py`, enforcement modes |
| **Depends on** | Phase I |

**Deliverables:**
- `load_and_validate_schema()` with network-first filtering
- `_validate_registrations()` with all check categories
- `SchemaSettings`, `SchemaViolationError`, `SchemaLoadError`
- `strict`/`warn`/`off` enforcement

**Acceptance:** An app with a valid network schema starts without warnings; an app
with missing channels in strict mode fails before MQTT connection.

#### Phase III — CLI and Developer Tooling (2–3 days)

| Attribute | Detail |
|-----------|--------|
| **Scope** | `_cli.py`, schema commands |
| **Depends on** | Phase I, Phase II |

**Deliverables:**
- `cosalette schema validate`, `check`, `dump`, `init`, `slice`
- CI gate integration (`task schema:check`)
- Taskfile entries

**Acceptance:** `cosalette schema check --app X:app --schema network.yaml` exits 0
for compliant apps and 1 with actionable output for non-compliant apps.

#### Phase IV — Consumer Code Generation (3–4 days)

| Attribute | Detail |
|-----------|--------|
| **Scope** | `_consumer_gen.py`, HA discovery CLI, OpenHAB CLI |
| **Depends on** | Phase I |

**Deliverables:**
- `HaDiscoveryGenerator` + `OpenHabGenerator`
- `cosalette ha-discovery generate/publish`
- `cosalette openhab things/items`

**Acceptance:** Generated HA discovery payloads are accepted by HA when published;
OpenHAB .things/.items files are syntactically valid.

#### Phase V — Publish-Time Validation and Network Monitoring (2–3 days)

| Attribute | Detail |
|-----------|--------|
| **Scope** | `ValidatingMqttPort`, schema status publishing, reload signal |
| **Depends on** | Phase II |

**Deliverables:**
- `ValidatingMqttPort` wrapper
- Schema status publishing to `{app}/schema/status`
- MQTT reload signal on `cosalette/schema/update`
- Network compliance monitor (standalone subscriber)
- Broker ACL contract for deploy principal, per-app principals, and monitor principal
- `cosalette schema acl` CLI command with multi-broker output formatters

**Acceptance:** Invalid payload in strict mode triggers error report and suppresses
publish. `cosalette schema acl` generates valid ACL configs for Mosquitto, EMQX, HiveMQ,
VerneMQ, and NanoMQ. Network monitor detects offline/non-compliant apps.

#### Phase VI — Documentation and ADR (1–2 days)

| Attribute | Detail |
|-----------|--------|
| **Scope** | ADR, developer guide, reference schema |
| **Depends on** | Phase II, Phase III |

**Deliverables:**
- ADR: MQTT Schema Enforcement (AsyncAPI + x-cosalette-* extensions)
- ADR: Schema Distribution (Ansible file deployment)
- Developer guide with worked examples
- Reference network schema for the example fleet

### 8.2 Gantt-Style Timeline

```
          Week 1        Week 2        Week 3        Week 4        Week 5
          ─────────────────────────────────────────────────────────────────
Phase I   ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Phase II  ░░░░░░░░████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Phase III ░░░░░░░░░░░░░░░░████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Phase IV  ░░░░░░░░░░░░░░░░░░░░░░░░██████████░░░░░░░░░░░░░░░░░░░░░░░░░░
Phase V   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░████████░░░░░░░░░░░░░░░░░░
Phase VI  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░██████░░░░░░░░░░░░
ADR       ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
          ─────────────────────────────────────────────────────────────────
          █ = active   ░ = waiting
```

Phase I–III are the critical path for the Ansible pre-deploy validation use case (UC1).
Phase IV (consumer codegen) can begin as soon as Phase I completes. Phase V
(publish-time validation, monitoring) can begin after Phase II.

### 8.3 Risk Register

| # | Risk | Prob | Impact | Mitigation |
|---|------|------|--------|------------|
| R1 | `jsonschema` too slow on Raspberry Pi | Low | Med | Pre-compiled validators, `off` mode for production |

> **Resolved (2026-04-09): Raspberry Pi payload validation is available in all modes,
> gated behind optional extras (`cosalette[schema]`).**
>
> - The optional extras pattern (`pip install cosalette[schema]`) means `pyyaml` and
>   `jsonschema` are only installed when the operator opts in.
> - If the extras aren't installed, `mode: off` is the only valid mode — the framework
>   gracefully degrades with an importable-check at startup.
> - If the extras ARE installed on a Pi, `warn` mode is safe — validation overhead is
>   per-publish, not per-message, and `jsonschema` validators are pre-compiled at
>   startup.
> - `strict` mode on a Pi is the operator's explicit choice — they decided the safety
>   is worth the overhead.
> - No need for a separate "dev-only" mode — the optional extras and mode setting
>   together cover all deployment scenarios.

| R2 | AsyncAPI 3.0.0 spec evolves, breaks extensions | Low | High | Pin to `3.0.0`, Option C fallback |
| R3 | Network schema too large to maintain | Med | Med | `schema init` + `schema slice` for bootstrapping; $ref for DRY |
| R4 | HA discovery format changes between HA versions | Med | Med | Pin to known HA version, test against HA MQTT integration |
| R5 | Developer adoption friction — schema feels like overhead | Med | High | Good defaults (warn mode), generators reduce manual work |
| R6 | Consumer metadata (x-cosalette-consumer) too verbose | Low | Low | Sensible defaults; auto-detect from JSON Schema where possible |

### 8.4 Success Criteria

The MQTT schema enforcement feature is **done** when:

1. A network schema at `/etc/cosalette/network-schema.yaml` defines all expected
   topics, payloads, and consumer metadata for the fleet.
2. `cosalette schema check` works as an Ansible deployment gate (UC1).
3. Apps load the network schema, filter to their slice, and validate registrations.
4. Payload validation at publish-time catches shape errors before they reach the broker.
5. `cosalette ha-discovery generate` produces valid HA discovery payloads.
6. `cosalette openhab things/items` produces valid OpenHAB configuration files.
7. `cosalette schema slice` extracts per-app schemas from the network schema.
8. All implementation meets coverage targets (unit, integration, property-based tests).
9. ADRs for schema enforcement and schema distribution are published in `docs/adr/`.
10. A developer guide with worked examples is published in `docs/guides/`.

### 8.5 Open Questions

1. **Should Approach A (decorator metadata) be implemented alongside Approach B?**
   Current leaning: **deferred**. Schema extensions are the primary source. If demand
   arises for apps that don't use the network schema, decorator metadata can supplement.

2. **Should the network monitor be a cosalette app or a standalone tool?**
   Current leaning: **standalone lightweight subscriber** — it doesn't need the full
   cosalette framework, just `aiomqtt` + `orjson`.

3. **How do complex HA components (climate, cover) map from the schema?**
   Current leaning: Explicit `x-cosalette-ha-discovery.component: climate` with manual
   configuration of the component-specific fields. Auto-detection handles simple
   components (sensor, binary_sensor, switch, number); complex ones require explicit
   extensions.

4. **Should generated consumer configs be committed to git or regenerated at deploy?**
   Current leaning: **generate at deploy time** via Ansible task. Avoids drift between
   schema and generated files.

5. **Sync vs async validation API?**
   Current leaning: **async loader, sync validator**, composed in async wiring function.

---

## Appendix A: AsyncAPI Deep-Dive Evaluation

> This appendix contains the full AsyncAPI 3.0.0 evaluation from iteration 1 (§2.1).
> Preserved here for reference. The condensed version is in §3.

_(The full deep-dive from iteration 1 §2.1–§2.3 is retained as-is. It includes:
channel/message model mapping, parameter handling, $ref and traits, MQTT bindings,
alternative format analysis (Options A–C), the hybrid approach design, worked examples,
and the complete scoring rationale.)_

See the [iteration 1 document](mqtt-schema-enforcement-v1.md) §2.1–§2.3 for the full
evaluation (~1000 lines).

---

## Appendix B: Iteration 1 Architecture Detail

> The full architecture designs from iteration 1 §4.1–§4.7 are preserved here for
> reference. They remain valid — the network-first model builds on top of this
> foundation.

The iteration 1 architecture covers:

- **§4.1 Schema Module Design** — Full `SchemaRegistry`, `ChannelSchema`,
  `OperationSchema`, `EnforcementConfig`, `CapabilityRequirement`, `MqttBinding`,
  `PayloadValidator` data models with worked vito2mqtt example
- **§4.2 Schema Loader Design** — `SchemaSource` protocol, `FileSchemaSource`,
  `MqttSchemaSource`, `HttpSchemaSource`, `InlineSchemaSource`, `$ref` resolution
  pipeline, channel/operation extraction, extension validation
- **§4.3 Lifecycle Integration** — Insertion point in `_run_async()`, `SchemaSettings`,
  `load_and_validate_schema()`, registration validation, enforcement modes, sequence
  diagram
- **§4.4 Payload Validation at Publish Time** — `ValidatingMqttPort`, performance
  analysis, error flow, skip-topic mechanism, lifecycle wiring
- **§4.5 CLI Architecture** — Typer entry point, `schema init/validate/check/dump`
- **§4.6 Testing Strategy** — Fixtures, unit tests, integration tests, Hypothesis
  strategies, coverage targets, `CosTestHarness` extensions
- **§4.7 Dependency Analysis** — `pyyaml`, `jsonschema`, optional extras, compatibility

See the [iteration 1 document](mqtt-schema-enforcement-v1.md) §4 for the full
architecture (~3500 lines).
