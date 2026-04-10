# Schema Control Topic Authorization

**Date:** 2026-04-09
**Context:** COS-cjg preparation for COS-5hx
**Status:** Recommended design for implementation planning

---

## 1. Problem

The network-first schema plan currently introduces two MQTT control surfaces:

- `cosalette/schema/update` to tell running apps to reload the local schema file
- `{app}/schema/status` and `cosalette/network/schema/status` to report compliance

Without an explicit authorization model, any principal that can publish to those topics
can trigger fleet-wide reloads, spoof compliance, or leave misleading retained state in
the broker.

The core design question is not just whether the topics exist. It is **who is allowed
to publish to which topic**, and whether the broker alone is a sufficient trust
boundary.

---

## 2. Threat Model

Assume a shared smart-home MQTT broker with multiple apps, automation tools, and manual
operator access.

Relevant threats:

- An unauthorized client publishes to `cosalette/schema/update` and causes repeated
  reloads or noisy validation churn.
- An app publishes to another app's `{app}/schema/status` topic and spoofs compliance.
- A client publishes forged retained data to `cosalette/network/schema/status` and
  misleads dashboards.
- A stale or malformed reload message causes apps to perform unnecessary work.

Out of scope for v1:

- A fully compromised broker.
- End-to-end authenticity in a multi-tenant or hostile broker environment.

If the broker itself is untrusted, MQTT ACLs are not enough and the design must move to
cryptographic message authenticity.

---

## 3. Options

### Option A: Broker ACLs With Shared Infrastructure Principal

Use broker authentication and ACLs, but keep a shared operational publisher for schema
control topics.

**Advantages:**

- Simple to explain and configure.
- Fits standard Mosquitto-style ACL models.
- No protocol changes inside cosalette.

**Disadvantages:**

- Weak publisher identity. Shared credentials collapse auditability.
- Easy to over-grant writes to `cosalette/#`.
- Does not prevent one app from spoofing another if credentials are reused.

### Option B: Unique Principals + Narrow Topic Ownership

Treat the broker as the trust boundary, but require a distinct MQTT principal for each
app instance plus separate principals for deployment and fleet monitoring.

**Advantages:**

- Strong enough for the intended single-operator smart-home deployment.
- Preserves MQTT-native authorization instead of inventing an overlay protocol.
- Clear topic ownership. Each principal can publish only its own namespace.
- Supports auditability because publishes map to real principals.

**Disadvantages:**

- Requires disciplined broker ACL management.
- Slightly more operational setup than shared credentials.
- Still trusts the broker to enforce identity correctly.

### Option C: Signed Control Messages in Addition to ACLs

Require cryptographic signatures on reload and status messages, with per-publisher keys
and replay protection.

**Advantages:**

- Defends against weak or misconfigured broker ACLs.
- Provides end-to-end publisher authenticity.
- More resilient if the broker is shared beyond a trusted home network.

**Disadvantages:**

- Key distribution, rotation, and replay handling add significant complexity.
- High implementation cost for low incremental value in the current deployment model.
- The reload topic carries only a hint, not the schema itself, so signatures solve a
  smaller problem than they first appear to.

---

## 4. Recommendation

Choose **Option B**.

This keeps the design aligned with MQTT's native security model: the broker enforces
who may publish and subscribe, while the framework treats the MQTT message as a hint and
reloads the authoritative schema from local disk.

This is the important architectural boundary:

- **Authoritative state:** `/etc/cosalette/network-schema.yaml`
- **Trigger only:** `cosalette/schema/update`
- **Trust anchor:** broker-authenticated principal + broker ACLs

Signed control messages are **not required for v1**.

Reasoning:

- The reload topic does not transport the schema. It only asks apps to re-read the file
  already deployed by Ansible.
- If an unauthorized publisher can write to `cosalette/schema/update`, the root problem
  is already broken broker authorization.
- Adding signatures now would impose security plumbing that exceeds the threat model and
  likely delays COS-5hx without materially improving the intended deployment.

Revisit signatures later if either of these becomes true:

- The broker is multi-tenant or not fully trusted.
- Control topics cross trust domains where broker ACLs are not authoritative.

---

## 5. Publisher Identity Model

Required principals:

- One **app principal per deployed app instance**. Example: `cosalette-vito2mqtt`.
- One **deployment principal** used by Ansible or an operator automation account.
- One **network-monitor principal** used by the fleet compliance aggregator.

**Limitation (v1):** The `cosalette schema acl` CLI generates one principal per
distinct `x-cosalette-app` value, not per deployment instance. If the same app is
deployed more than once (e.g. on different hosts), those instances share a principal
and audit trails collapse. Adding an instance dimension (host/instance ID) to the
schema metadata is a candidate enhancement for a future version.

Rules:

- App principals publish only their own app namespace, including `{app}/schema/status`.
- App principals subscribe to `cosalette/schema/update`.
- App principals must **not** publish to `cosalette/schema/update`.
- App principals must **not** publish to `cosalette/network/schema/status`.
- The deployment principal may publish to `cosalette/schema/update`.
- The network-monitor principal may publish to `cosalette/network/schema/status`.
- No shared wildcard write access to `cosalette/#` for ordinary apps.

This follows the same separation-of-concerns logic as app LWT and heartbeat handling in
ADR-012: broker-mediated identity is the mechanism, not application-level mutual trust.

---

## 6. Topic Ownership Matrix

| Topic | Publisher | Subscriber | Notes |
|---|---|---|---|
| `cosalette/schema/update` | Deployment principal only | All cosalette apps | Trigger only. Not authoritative config. |
| `{app}/schema/status` | Owning app principal only | Monitor, dashboards, operators | Retained compliance status for one app. |
| `cosalette/network/schema/status` | Network-monitor principal only | Dashboards, operators | Retained aggregate fleet status. |

Broker ACL intent:

- `cosalette-vito2mqtt` may write `vito2mqtt/#` and read `cosalette/schema/update`.
- `cosalette-vito2mqtt` may not write `cosalette/#`.
- `cosalette-deploy` may write `cosalette/schema/update` and does not need access to
  app-owned status topics.
- `cosalette-monitor` may read `+/schema/status`, `+/status`, and write
  `cosalette/network/schema/status`.

---

## 7. Reload Message Contract

The reload topic payload should remain small and non-authoritative:

```json
{
  "schema_version": "2.1.0",
  "issued_at": "2026-04-09T12:00:00Z",
  "request_id": "deploy-2026-04-09-120000",
  "issuer": "ansible"
}
```

Requirements:

- Apps must treat the message as a **reload hint**, not as configuration content.
- Apps must ignore malformed JSON or payloads missing required fields.
- Apps reload from the configured local schema path and validate that file.
- Apps publish a fresh `{app}/schema/status` result after reload succeeds or fails.

This keeps the wire protocol narrow and reduces the blast radius of any message spoofing
attempt that gets past broker ACLs.

---

## 8. Operational Posture — Recommended, Not Required

### 8.1 Zero Burden for Non-Users

If schema enforcement is disabled (`mode: off`, which is the default), no control
topics exist. The framework publishes nothing to `{app}/schema/status`, subscribes to
nothing on `cosalette/schema/update`, and the network monitor has no work to do. Users
who do not enable schema enforcement have **no new broker configuration requirements**.

The regular data topics (`{app}/{device}/state`, `{app}/{device}/set`, etc.) continue to
work exactly as before — anonymous, shared credentials, or per-app ACLs, whatever the
operator already uses.

### 8.2 ACLs Are Recommended, Not Enforced by cosalette

The framework cannot verify broker configuration at runtime. It cannot check whether the
broker has ACLs enabled, whether the current principal is correctly scoped, or whether
another client is mis-authorized. This is by design — the broker is the trust boundary,
and cosalette operates within it rather than around it.

The documentation should state clearly:

- Per-app ACLs are **recommended** for any deployment that enables schema enforcement.
- cosalette **does not require** ACLs to function. The framework will work on an
  anonymous broker, but the operator accepts the risk that any client can publish to
  control topics.
- The `cosalette schema acl` command (see §11) generates broker-specific ACL
  configurations to reduce the operational burden.

### 8.3 Broker-Agnostic Design

The topic ownership rules (§6) are expressed as **intent**, not as any broker's native
syntax. The ADR should use broker-agnostic phrasing. Broker-specific syntax belongs in
the developer guide and in `cosalette schema acl` output — not in the architectural
decision itself.

The relevant concepts — username-based authentication and per-topic
publish/subscribe permissions — are widely available across MQTT deployments but are
not standardized uniformly. MQTT 5.0 defines authentication mechanisms and related
properties (including Enhanced Authentication in §4.12), while per-topic
publish/subscribe permissions are provided as broker ACL features whose syntax and
semantics vary by broker. cosalette treats ACLs as a common broker capability rather
than as behaviour defined by the MQTT specification.

---

## 9. Acceptance Criteria for COS-cjg

- The schema-enforcement plan names a concrete authorization boundary for reload and
  schema-status topics.
- Publisher identity is explicit for app, deploy, and monitor principals.
- The design states that **broker ACLs are recommended** for control topics.
- The design states that **signed messages are not required in v1** and records the
  conditions that would justify revisiting that choice.
- Future implementation work inherits a test strategy for authorization behaviour.
- The `cosalette schema acl` CLI command is specified with multi-broker support.

---

## 10. Test Strategy for Future Implementation

Unit tests:

- Validate reload payload parsing and rejection of malformed or incomplete messages.
- Validate topic ownership helpers or configuration builders if the framework exposes
  them.

Integration tests:

- Start a test broker with ACLs enabled.
- Verify an app principal cannot publish to `cosalette/schema/update`.
- Verify one app principal cannot publish to another app's `{app}/schema/status`.
- Verify the deployment principal can publish reload hints.
- Verify the monitor principal can publish aggregate network status.

System tests:

- Deploy a new schema file, publish a reload hint, and confirm apps reload from disk.
- Attempt a spoofed publish with insufficient credentials and confirm the broker denies
  it.

The important point is that authorization must be tested at the broker boundary, not
only inside cosalette. A pure in-process harness is insufficient for ACL behaviour.

---

## 11. `cosalette schema acl` — ACL Generator CLI

### Purpose

Generate broker-specific ACL configuration snippets from a network schema. This
reduces the operational burden of maintaining per-app ACL rules by deriving them
mechanically from the same schema that drives validation.

### Interface

```text
cosalette schema acl --schema <path> [--broker <name>] [--deploy-user <name>] [--monitor-user <name>]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--schema <path>` | (required) | Path to the network schema file |
| `--broker <name>` | `mosquitto` | Target broker format (see supported brokers) |
| `--deploy-user` | `cosalette-deploy` | Username for the deployment principal |
| `--monitor-user` | `cosalette-monitor` | Username for the network monitor principal |

App usernames are derived automatically from the schema: for each distinct
`x-cosalette-app` value, the tool generates a principal named
`cosalette-{app_name}`.

**Input validation:** The `x-cosalette-app` value must match a conservative slug
regex (`^[a-z0-9][a-z0-9-]*$`). Values containing spaces, quotes, `#`, `/`, or
other metacharacters must be rejected during schema parsing — before they reach any
broker-specific formatter. Each formatter must additionally escape or quote values
appropriately for its output syntax.

### Supported Brokers

| `--broker` value | Broker | Output format | Config location |
|------------------|--------|---------------|------------------|
| `mosquitto` | Eclipse Mosquitto | `topic read/write` ACL file | `acl_file` in `mosquitto.conf` |
| `emqx` | EMQX | Erlang-term ACL rules | `etc/acl.conf` or built-in authz |
| `hivemq` | HiveMQ | File-based XML permissions | Extension config |
| `vernemq` | VerneMQ | `vmq_acl` plugin rules | `vmq.acl` |
| `nanomq` | NanoMQ | `auth.acl` config rules | `nanomq.conf` |

New brokers can be added as output formatters without changing the intent logic.
The core derives the same set of `(principal, topic, permission)` tuples regardless
of the target broker.

### Example Output

#### Mosquitto (default)

```text
$ cosalette schema acl --schema /etc/cosalette/network-schema.yaml

# Generated by: cosalette schema acl
# Schema: Smart Home MQTT Network v2.1.0
# Broker: mosquitto
#
# App principals

user cosalette-vito2mqtt
topic write vito2mqtt/#
topic read vito2mqtt/#
topic read cosalette/schema/update

user cosalette-airthings2mqtt
topic write airthings2mqtt/#
topic read airthings2mqtt/#
topic read cosalette/schema/update

# Deployment principal

user cosalette-deploy
topic write cosalette/schema/update

# Network monitor principal

user cosalette-monitor
topic read +/schema/status
topic read +/status
topic write cosalette/network/schema/status
```

#### EMQX

```text
$ cosalette schema acl --schema /etc/cosalette/network-schema.yaml --broker emqx

%% Generated by: cosalette schema acl
%% Schema: Smart Home MQTT Network v2.1.0
%% Broker: emqx

{allow, {user, "cosalette-vito2mqtt"}, publish, ["vito2mqtt/#"]}.
{allow, {user, "cosalette-vito2mqtt"}, subscribe, ["vito2mqtt/#"]}.
{allow, {user, "cosalette-vito2mqtt"}, subscribe, ["cosalette/schema/update"]}.

{allow, {user, "cosalette-airthings2mqtt"}, publish, ["airthings2mqtt/#"]}.
{allow, {user, "cosalette-airthings2mqtt"}, subscribe, ["airthings2mqtt/#"]}.
{allow, {user, "cosalette-airthings2mqtt"}, subscribe, ["cosalette/schema/update"]}.

{allow, {user, "cosalette-deploy"}, publish, ["cosalette/schema/update"]}.

{allow, {user, "cosalette-monitor"}, subscribe, ["+/schema/status"]}.
{allow, {user, "cosalette-monitor"}, subscribe, ["+/status"]}.
{allow, {user, "cosalette-monitor"}, publish, ["cosalette/network/schema/status"]}.

{deny, all}.
```

#### HiveMQ

```text
$ cosalette schema acl --schema /etc/cosalette/network-schema.yaml --broker hivemq

<!-- Generated by: cosalette schema acl -->
<!-- Schema: Smart Home MQTT Network v2.1.0, Broker: hivemq -->
<file-rbac>
  <client-credentials>
    <name>cosalette-vito2mqtt</name>
    <permissions>
      <publish topic="vito2mqtt/#" />
      <subscribe topic="vito2mqtt/#" />
      <subscribe topic="cosalette/schema/update" />
    </permissions>
  </client-credentials>
  <client-credentials>
    <name>cosalette-airthings2mqtt</name>
    <permissions>
      <publish topic="airthings2mqtt/#" />
      <subscribe topic="airthings2mqtt/#" />
      <subscribe topic="cosalette/schema/update" />
    </permissions>
  </client-credentials>
  <client-credentials>
    <name>cosalette-deploy</name>
    <permissions>
      <publish topic="cosalette/schema/update" />
    </permissions>
  </client-credentials>
  <client-credentials>
    <name>cosalette-monitor</name>
    <permissions>
      <subscribe topic="+/schema/status" />
      <subscribe topic="+/status" />
      <publish topic="cosalette/network/schema/status" />
    </permissions>
  </client-credentials>
</file-rbac>
```

### Architecture

The command is a pure function over the network schema:

1. Parse the AsyncAPI document.
2. Extract all distinct `x-cosalette-app` values → app principal list.
3. Build the canonical `(principal, topic, permission)` tuple set using the topic
   ownership rules from §6.
4. Format the tuple set using the selected broker's output formatter.

Each formatter is a small function that takes the tuple set and returns a string.
Adding a new broker requires only a new formatter — no changes to the intent logic.

The command has **no runtime dependencies beyond the schema parser** (PyYAML). It does
not connect to the broker, does not validate credentials, and does not deploy the ACL
file. It is a code-generation tool, not a configuration management tool.

### Ansible Integration

The generated output is designed to be consumed by Ansible:

```yaml
# deploy-mqtt-acl.yml
- name: Generate ACL from network schema
  ansible.builtin.command: >
    cosalette schema acl
      --schema /etc/cosalette/network-schema.yaml
      --broker mosquitto
  register: acl_output

- name: Deploy ACL file
  ansible.builtin.copy:
    content: "{{ acl_output.stdout }}"
    dest: /etc/mosquitto/acl.conf
  notify: reload mosquitto
```

Alternatively, generate once and commit the ACL file to the infrastructure repo.

---

## 12. Impact on COS-5hx

COS-5hx can now proceed with a concrete constraint set:

- Phase V should be described as **authorized reload signaling and schema-status
  publishing**, not just adding more MQTT topics.
- The implementation plan should assume **per-app MQTT principals** as a deployment
  prerequisite (recommended, not enforced).
- The first implementation slice should prefer **file deploy + reload hint** over any
  design that publishes full schema documents through MQTT.
- The CLI plan should include `cosalette schema acl` as a Phase V deliverable.
