---
status: Accepted
date: 2026-08-27
impact: moderate
tags: [security, mqtt, configuration]
---

# ADR-062: Default MQTT TLS to Enabled at the Next 0.x Minor Release

## Status

Accepted **Date:** 2026-08-27

## Context

Security-audit finding F-CU1 (2026-08 audit, CWE-1188/319, Medium): `MqttSettings.tls` (`packages/src/cosalette/_settings/__init__.py`) defaults to `False`. Framework apps that connect to a non-local broker without explicitly setting `MQTT__TLS=true` transmit MQTT credentials — and any application payload — in plaintext. The interim mitigation, already shipped, is `MqttClient._log_transport_posture()` (`packages/src/cosalette/_mqtt/_client.py`): at connect time it checks `not self.settings.tls and host not in {"localhost", "127.0.0.1", "::1"}` and logs a `logger.warning` naming either plaintext-credential exposure (when username/password are set) or anonymous-join risk (when they are not). This warning is observability only — it does not change behaviour, and a deployment that never inspects its logs sees no signal at all.

The audit roadmap (structural item #3) calls for closing this by flipping the default itself: 'Default-on TLS / required explicit opt-out at 0.7.0 (F-CU1) — minor bump.' The current released version is 0.6.3 (`pyproject.toml`); per this project's actual release convention (ADR-045: breaking changes 'belong at the 0.x.0 release boundary') and the precedent ADR-060 set by shipping its own breaking handler-timeout default directly as a 0.x minor bump with no deferral, the next 0.x minor release is 0.7.0 — not a '1.0' milestone, which this project has no convention for (ADR-061 originally drafted the same mistaken '1.0' language and was corrected in place on 2026-08-27, before it had any release impact).

The existing `_LOCAL_HOSTS` heuristic (`{"localhost", "127.0.0.1", "::1"}`) is used only to decide whether to *warn*; there is no existing mechanism that varies the `tls` *default value* itself by host, and pydantic-settings field defaults are static — building a host-conditional default would require new resolution machinery (e.g. a `model_validator` reading `host` before `tls` is finalised) that does not exist today and is out of scope for this decision.

A closely related finding, F-CU2 (anonymous join of non-local brokers by default, CWE-1188, Low-Med), shares the same interim warning but carries no default-flip commitment in the audit roadmap — it stays out of scope here and is not addressed by this ADR.

## Decision

Flip `MqttSettings.tls`'s default from `False` to `True`, shipping in **0.7.0** (the next 0.x minor release after 0.6.3), because a secure-by-default posture closes the common case of F-CU1 without waiting for an app author to discover and set `MQTT__TLS=true` themselves. The new default applies unconditionally — it does not carve out `localhost`/loopback brokers, because no mechanism to vary a pydantic field default by another field's value exists in this codebase today, and inventing one solely to preserve today's no-TLS local-dev convenience would add resolution machinery disproportionate to a default that any developer can override with one env var (`MQTT__TLS=false`) or constructor argument. Apps that connect to a broker without TLS support — including most local/dev brokers, which typically do not terminate TLS — must set `tls=False` explicitly starting in 0.7.0; the existing `_validate_tls_settings` model validator and `_build_ssl_context` logic are unaffected, since they already branch on `self.settings.tls` rather than assuming a particular default.

The existing `_log_transport_posture()` startup warning is kept unchanged as defense-in-depth: it still fires for any app that explicitly opts back out to `tls=False` on a non-local host, exactly as it does today. This decision does not touch F-CU2 (anonymous-join warning), which remains a separate, out-of-scope finding with no default-flip commitment.

```python
# packages/src/cosalette/_settings/__init__.py (0.7.0)
class MqttSettings(BaseModel):
    ...
    tls: bool = Field(
        default=True,  # was False through 0.6.x
        description="Enable TLS for the MQTT client connection.",
    )

# Apps on a broker without TLS support must now opt out explicitly:
settings = MqttSettings(host="broker.local", tls=False)
# or: MQTT__TLS=false

# Loopback dev brokers are NOT exempted from the new default — a local
# mosquitto without TLS listeners needs the same explicit opt-out:
settings = MqttSettings(host="localhost", tls=False)
```

## Decision Drivers

- F-CU1 (CWE-1188/319, Medium): plaintext MQTT credentials on non-local brokers when tls=False, currently only mitigated by a startup warning that changes no behaviour
- Consistency with this project's actual breaking-change convention: ADR-045 places breaking changes at the next 0.x.0 boundary, and ADR-060 shipped its own breaking default directly as a 0.x minor bump with no 1.0 deferral — this decision follows the same pattern rather than repeating ADR-061's original (and since-corrected) '1.0' mistake
- No existing mechanism ties a pydantic-settings field default to another field's value (e.g. host); a host-conditional default for tls would be new, unbuilt resolution machinery and is out of scope for this decision
- Defense-in-depth: the already-shipped _log_transport_posture() warning must keep working for the explicit tls=False opt-out path, since removing a working mitigation would regress observability for apps that legitimately need it
- Migration cost must be a single, well-documented keyword (tls=False) or env var (MQTT__TLS=false), mirroring the opt-out ergonomics ADR-060 and ADR-061 established for other security-hardening defaults

## Considered Options

### Option 1: Status quo (warning only)

Leave MqttSettings.tls defaulting to False indefinitely and rely solely on the _log_transport_posture() startup warning to alert operators to an insecure configuration.

- *Advantages:* Zero migration risk — no existing app's connection behaviour changes; No release-boundary coordination or changelog/migration-note work required
- *Disadvantages:* F-CU1 stays open indefinitely — the audit roadmap's structural item #3 is never closed; A warning is easy to miss: apps that don't route stderr to a monitored sink, or that filter WARNING-level logs, get no protection at all; New apps scaffolded from the framework's own templates default to plaintext transport unless the author reads the warning and understands its implication

### Option 2: Scheduled default flip at 0.7.0 (chosen)

Flip MqttSettings.tls's default to True, unconditionally (no local-broker carve-out), shipping as part of the 0.7.0 release — the next 0.x minor version boundary after 0.6.3. Document the change now via this ADR; implement the code change at the 0.7.0 release itself. Keep the existing startup warning for the explicit opt-out path.

- *Advantages:* Closes F-CU1 for the common case: new and upgraded apps get TLS by default without any action; Consistent with the project's actual 0.x-minor breaking-change convention (ADR-045, ADR-060 precedent) — no artificial 1.0 deferral; Single, well-known opt-out (tls=False / MQTT__TLS=false) — same mental model as other security-hardening defaults (ADR-060's timeout=None, ADR-061's disclose_messages_for); Unconditional default keeps the change simple: no new host-conditional resolution logic to build, test, and maintain
- *Disadvantages:* Breaking change: every app connecting to a broker without TLS support (a common case for local/dev brokers and some legacy production brokers) must add an explicit tls=False before upgrading to 0.7.0; Requires a migration note / changelog entry at 0.7.0 release time so app authors aren't surprised by a suddenly-failing (or silently-different) connection; Local/dev ergonomics regress slightly: a developer pointing at a plain-TCP mosquitto on localhost now needs one extra setting, even though the loopback warning exemption shows the project already treats local brokers as lower-risk for warning purposes

### Option 3: Immediate breaking flip on the current 0.x line

Change the default to True immediately, in the very next patch/minor release after this ADR is accepted, rather than waiting for the already-planned 0.7.0 boundary.

- *Advantages:* Closes F-CU1 sooner than waiting for 0.7.0; No risk of the flip being forgotten or dropped from the 0.7.0 scope
- *Disadvantages:* Contradicts this project's release-boundary convention (ADR-045) of landing breaking changes at a deliberate 0.x.0 boundary rather than an arbitrary point release; 0.7.0 is already the audit roadmap's committed target for this exact finding (structural item #3) and is not blocked on anything else — moving it earlier buys little and creates two uncoordinated breaking-change release trains instead of one; Gives app authors less lead time to notice this ADR and prepare, since it wouldn't align with a clearly-versioned minor release they already watch for breaking changes

## Decision Matrix

| Criterion | Status quo (warning only) | Scheduled default flip at 0.7.0 (chosen) | Immediate breaking flip on the current 0.x line |
| --- | --- | --- | --- |
| Closes F-CU1 for the common case | 1 | 5 | 5 |
| Consistency with ADR-045/ADR-060 release-boundary convention | 3 | 5 | 2 |
| Migration risk contained (clear opt-out, adequate lead time) | 5 | 4 | 2 |
| Implementation/maintenance simplicity (no new resolution machinery) | 5 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- New and upgraded apps get a TLS-enabled MQTT connection by default from 0.7.0 onward, closing F-CU1 for the common case without requiring any action from app authors
- The opt-out is a single, already-familiar keyword/env-var (tls=False / MQTT__TLS=false), matching the mental model ADR-060 and ADR-061 established for other security-hardening defaults
- The existing _log_transport_posture() warning keeps working unchanged as defense-in-depth for apps that explicitly opt back out on a non-local broker
- Ships on this project's actual breaking-change convention (the next 0.x.0 boundary, per ADR-045) rather than inventing or repeating a non-existent '1.0' milestone

### Negative

- Breaking change: apps connecting to a broker without TLS support (common for local/dev brokers and some legacy production brokers) must add an explicit tls=False before upgrading to 0.7.0, or their connection will attempt (and likely fail) a TLS handshake the broker doesn't support
- Requires a migration note and changelog entry at 0.7.0 release time so the breaking change is discoverable ahead of the upgrade, not discovered via a connection failure
- The new default applies unconditionally, including to loopback/local brokers, so local development workflows that relied on the implicit False default now need an explicit override even though the project's own warning logic already treats loopback as lower-risk
- F-CU2 (anonymous join of non-local brokers by default) remains unaddressed by this decision — it stays mitigated by warning only, with no default-flip commitment, and would need its own separate ADR if that changes

_2026-08-27_
