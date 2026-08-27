---
status: Accepted
date: 2026-08-27
impact: moderate
tags: [security, persistence, mqtt, configuration]
---

# ADR-063: Optional HMAC-Signed Retained-Cleanup Snapshots

## Status

Accepted **Date:** 2026-08-27

## Context

Security-audit finding F-DP3 (`docs/security/audit-report.md`, DOC-GAP, Low, CWE-345/367): the ADR-048 orphaned-retained-topic cleanup mechanism (`packages/src/cosalette/_wiring/_retained_cleanup.py`) persists each run's resolved entity set under a reserved key in the app's configured `Store`, then diffs it against the previous run's snapshot to clear removed entities' retained MQTT topics. The module's docstring already documents its single-writer assumption ('one running app instance per `(store, prefix)` pair; concurrent instances sharing both can last-save-win the persisted entity set and mis-diff retained entities') — F-DP3 is a documentation gap about that assumption, not an open remediation item, and the audit report's refuted-leads section already establishes that a tampered or malformed snapshot's blast radius is capped by existing validation: `_orphan_topics` runs `validate_mqtt_name` on every entity name before it can appear in a publish target, checks `is_root is True` with strict identity (a corrupted `"False"` string cannot widen scope to root-level topics), and restricts cleared kinds to a hardcoded `state`/`availability` allowlist (never `/set`, `error`, `status`, `_meta`, or `schema`). A live-topic-wipe risk from a tampered snapshot was investigated and refuted on this basis.

What that existing validation does **not** provide is integrity: any writer with access to the `Store` backend (e.g. a shared SQLite file, a network-backed store, or a store on a filesystem with looser-than-intended permissions) can silently rewrite the persisted snapshot, and `reconcile_retained_topics` has no way to detect that the data it loaded was not the value it last saved. The audit roadmap (item 5) flagged HMAC-signing the snapshot as an *optional future hardening* add-on — tamper detection against an untrusted-but-writable `Store` backend — not a remediation of the documented single-writer race. Signing does **not** fix that race: a second legitimate writer holding a valid key still last-save-wins the snapshot, exactly as an unsigned snapshot does today; this ADR's scope is limited to detecting unauthorized/unkeyed tampering, not concurrency control. Because the existing risk is already Low and already capped by validation, this feature ships as an opt-in hardening option rather than a default-on behavior change — apps that do not configure a key see zero behavior change.

Grep of the codebase confirms zero existing `hmac` usage anywhere in `packages/src/cosalette/`; the only existing `hashlib` usage (`_runners/_command_runner.py`) is a non-security debug fingerprint (truncated `sha256` hex for log correlation), not an authentication primitive. This ADR is therefore the framework's first real cryptographic-authentication code path, so the key-provisioning question needs explicit treatment rather than being left implicit.

## Decision

Add an opt-in `retained_cleanup_snapshot_key: SecretStr | None = None` parameter to `App.__init__` (mirroring the existing `retained_cleanup: bool | None` tri-state override on the same class and the `disclose_messages_for`/`heartbeat_include_version` opt-in precedents from ADR-060/ADR-061), threaded down to `reconcile_retained_topics()` in `_wiring/_retained_cleanup.py`. `None` (the default) preserves today's unsigned behavior exactly — zero change for existing apps. When a key is supplied, `reconcile_retained_topics` computes an HMAC-SHA256 over a canonical (`sort_keys=True`, fixed separators) JSON serialization of the snapshot's `hmac_alg`, `schema_version`, and `entities` fields — including `hmac_alg` in the authenticated payload prevents algorithm-selector tampering (an attacker who can write the Store cannot change `hmac_alg` to a different value without also invalidating the digest). The `hmac_alg` field is stored alongside the digest as `"hmac-sha256"` so a future algorithm migration is self-describing without touching `_SNAPSHOT_SCHEMA_VERSION`; implementations must reject any `hmac_alg` value they do not recognise before attempting verification. On load, when a key is configured, verification uses `hmac.compare_digest` (timing-safe) against a freshly computed digest of the loaded payload; on any mismatch, missing signature field (e.g. a snapshot written before the key was configured, or written by an unkeyed run), or malformed envelope, the loaded snapshot is treated as absent — the exact same fail-closed path `_removed_entities` already takes for an unrecognized `schema_version` (log a warning, treat as no previous snapshot, skip this run's cleanup, and overwrite the stored snapshot with a freshly signed one). This keeps the module's existing fail-closed invariant (`_removed_entities`' behavior on schema mismatch; the outer `try/except Exception` in `reconcile_retained_topics` that never breaks startup) uniform across both the pre-existing schema-version check and the new signature check, and avoids introducing a second, differently-shaped failure mode. When no key is configured, the loader never looks for or requires the `hmac_sha256`/`hmac_alg` fields, so a signed snapshot read by an unkeyed run degrades gracefully to 'previous snapshot absent' via the same missing-field path, rather than crashing.

`_SNAPSHOT_SCHEMA_VERSION` continues to describe the shape of `entities` only; it is not bumped for this feature. The signature fields live one level up, alongside `schema_version` and `entities` in the persisted dict, so a `Store` reading old-format (unsigned) data during a keyed run and new-format (signed) data during an unkeyed run are both representable as 'no signature present' without any schema-version negotiation — the envelope is additive, not a breaking reshape.

```python
# Opt-in: app supplies a signing key (e.g. from its own secrets management).
# None (default, omitted) preserves today's unsigned behavior exactly.
app = cosalette.App(
    name="my-app",
    retained_cleanup_snapshot_key=SecretStr(os.environ["CLEANUP_SNAPSHOT_KEY"]),
)

# Persisted envelope shape when a key is configured (packages/src/cosalette/_wiring/_retained_cleanup.py):
{
    "schema_version": 1,
    "entities": {...},          # unchanged shape
    "hmac_alg": "hmac-sha256",  # new, only present when signed
    "hmac_sha256": "<hex digest over canonical hmac_alg+schema_version+entities JSON>",
}

# Verification on load (conceptual):
# hmac_alg must be a known value before use; include it in the digest to bind the selector.
if loaded.get("hmac_alg") != "hmac-sha256":
    ...  # fail-closed: unknown algorithm treated as missing signature
expected = hmac.new(key, canonical_json(hmac_alg, schema_version, entities), hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, loaded.get("hmac_sha256", "")):
    # fail-closed: treat as no previous snapshot, same path as unknown schema_version
    ...
```

## Decision Drivers

- F-DP3 (CWE-345/367, Low): HMAC-signing is an optional tamper-detection hardening add-on against an untrusted-but-writable Store backend — it must not be conflated with, or presented as fixing, the already-documented single-writer concurrency race (a second legitimate keyholder still last-save-wins)
- The risk being closed is already Low and already capped by existing defense-in-depth (validate_mqtt_name, strict is_root is True check, hardcoded state/availability kind allowlist) per the audit report's refuted live-topic-wipe lead, so the feature must be strictly opt-in/additive — no behavior change for apps that do not configure a key
- Key provisioning must not defeat its own purpose: a key stored via the same Store the snapshot is written to would let an attacker who can already write the snapshot rewrite the key too, so the key must come from a channel independent of the Store backend being protected
- This is the framework's first HMAC/cryptographic-authentication code path (confirmed zero existing hmac usage; the sole hashlib usage elsewhere is a non-security debug fingerprint) — it should follow, not invent, this codebase's established opt-in-parameter conventions (App(disclose_messages_for=...), App(heartbeat_include_version=...), App(retained_cleanup=...))
- Fail-closed consistency: the new signature-verification failure path must reuse the same 'treat as no previous snapshot, log, continue' shape _removed_entities already uses for an unrecognized schema_version, rather than introducing a second failure taxonomy in the same module
- Cost to apps that do not opt in must be exactly zero — no new mandatory settings, no new reserved environment variable namespace collision risk (per the F-TP1 finding's caution about that namespace)

## Considered Options

### Option 1: App-supplied opt-in key (chosen)

Add `retained_cleanup_snapshot_key: SecretStr | None = None` to `App.__init__`, threaded through to `reconcile_retained_topics()`. The app author is responsible for sourcing the key's value (env var, secrets manager, mounted file, etc.) exactly as they already do for MQTT broker credentials (`MqttSettings.password: SecretStr | None`). `None` disables signing entirely and is the default.

- *Advantages:* Key channel is independent of the Store backend being protected — an attacker who can write the snapshot via a compromised Store cannot also rewrite the key, since the key never passes through Store; Matches this codebase's established opt-in-parameter pattern exactly (disclose_messages_for, heartbeat_include_version, retained_cleanup, MqttSettings.password all use the same SecretStr-or-tri-state shape); Zero behavior change for every existing app; zero new reserved environment variable name (the app chooses how it sources the SecretStr value, same as MQTT credentials); Composes with any Store backend uniformly — signing logic lives entirely in _retained_cleanup.py and never needs backend-specific trust assumptions
- *Disadvantages:* Puts key management (generation, rotation, storage) on the app author, who must understand this is a app-level secret parallel to their MQTT credentials; An app author who does not read this ADR or the parameter docstring simply never adopts it — the Low-severity gap the audit refuted stays technically open by default (an accepted tradeoff, since it is opt-in hardening, not a default-on fix); No first-run migration signal: an app that adopts a key after already having an unsigned snapshot on disk will fail-closed on that first load (see Decision) rather than silently trusting the old data — a one-time, documented transition cost

### Option 2: Framework auto-generates and persists the key

On first run, generate a random key (e.g. `secrets.token_bytes(32)`) and persist it under a second reserved Store key, with no app-facing parameter at all — signing becomes transparently on-by-default.

- *Advantages:* Zero app-facing API surface or documentation burden — signing 'just works' with no opt-in step; No risk of an app author forgetting to configure a key, since there is nothing to configure
- *Disadvantages:* Self-defeating against the exact threat model this ADR targets: if the key is persisted via the same Store as the snapshot, an attacker who can write the snapshot can also rewrite the stored key and re-sign a tampered snapshot with it, providing no tamper detection at all; Avoiding that flaw would require a second, differently-trusted persistence channel for the key alone (e.g. a separate file with tighter permissions) — new machinery this ADR would have to invent and that does not exist today, disproportionate to a Low-severity, already-capped finding; A silently-generated key an app author never sees cannot be rotated, escrowed, or audited by them, which is worse security hygiene than an explicit app-supplied secret for a mechanism whose entire point is tamper *detection* an operator can reason about

### Option 3: Derive the key from an existing app secret (e.g. MQTT password)

Reuse `MqttSettings.password` (or another existing app-configured secret) as an HKDF input to derive the HMAC key, avoiding a new parameter entirely.

- *Advantages:* No new parameter to add or document — reuses a secret the app already provides; No new key-management burden distinct from what the app already carries for broker authentication
- *Disadvantages:* Cross-purpose key reuse: binds an unrelated security property (retained-cleanup snapshot integrity) to the lifecycle of the broker credential, so rotating the MQTT password silently invalidates every previously-signed snapshot with no independent signal, and a broker credential leak becomes a snapshot-signing-key leak too; Many apps run without any broker password configured at all (anonymous/local brokers per the audit's ASVS notes), in which case there is no secret to derive from and the feature would silently be unavailable or require a separate fallback anyway; Makes the threat model harder to reason about: 'what does this key protect' no longer has a single, self-contained answer, complicating any future audit of this code path

## Decision Matrix

| Criterion | App-supplied opt-in key (chosen) | Framework auto-generates and persists the key | Derive the key from an existing app secret (e.g. MQTT password) |
| --- | --- | --- | --- |
| Actually defeats the target threat (attacker who can write Store) | 5 | 1 | 3 |
| Consistency with existing opt-in-parameter conventions | 5 | 2 | 2 |
| Zero behavior change / migration risk for non-adopting apps | 5 | 2 | 4 |
| Implementation simplicity (no new persistence/derivation machinery) | 5 | 2 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Apps that configure a key get genuine tamper detection against an untrusted-but-writable Store backend: any snapshot rewritten without the key is detected via hmac.compare_digest and discarded (fail-closed), rather than silently trusted
- Zero behavior change, zero new required configuration, and zero new reserved environment-variable names for the overwhelming majority of apps that do not opt in — the default (None) preserves today's unsigned mechanism exactly
- Reuses the module's existing fail-closed shape (the same 'log, treat as absent, continue' path _removed_entities already takes for a schema-version mismatch) instead of inventing a second failure taxonomy, keeping the module's single mental model for 'previous snapshot cannot be trusted'
- Establishes a clean, additive envelope (hmac_alg + hmac_sha256 alongside the unchanged schema_version/entities shape) that does not require bumping _SNAPSHOT_SCHEMA_VERSION and stays forward-compatible with a future algorithm change

### Negative

- Does not address, and must not be read as addressing, the module's documented single-writer concurrency race — two legitimate instances sharing both a key and a (store, prefix) pair still last-save-win the snapshot exactly as they do unsigned today
- Adds app-facing key-management burden (generation, secure storage, rotation) that the app author is fully responsible for, parallel to but distinct from their existing MQTT credential management
- A one-time fail-closed transition cost when a key is first configured on an app with an existing unsigned snapshot on disk: that snapshot has no hmac_sha256 field, so the first keyed run treats it as absent, skips that run's orphaned-topic cleanup, and writes a freshly signed snapshot — a documented, expected startup log line rather than a silent failure
- Grows the App constructor's settings surface by one more optional parameter to document (guides, reference/errors-style docs, ai help topics) alongside disclose_messages_for, heartbeat_include_version, and retained_cleanup

_2026-08-27_
