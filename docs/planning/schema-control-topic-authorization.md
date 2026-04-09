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

## 8. Acceptance Criteria for COS-cjg

- The schema-enforcement plan names a concrete authorization boundary for reload and
  schema-status topics.
- Publisher identity is explicit for app, deploy, and monitor principals.
- The design states that **broker ACLs are mandatory** for control topics.
- The design states that **signed messages are not required in v1** and records the
  conditions that would justify revisiting that choice.
- Future implementation work inherits a test strategy for authorization behaviour.

---

## 9. Test Strategy for Future Implementation

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

## 10. Impact on COS-5hx

COS-5hx can now proceed with a concrete constraint set:

- Phase V should be described as **authorized reload signaling and schema-status
  publishing**, not just adding more MQTT topics.
- The implementation plan should assume **per-app MQTT principals** as a deployment
  prerequisite.
- The first implementation slice should prefer **file deploy + reload hint** over any
  design that publishes full schema documents through MQTT.