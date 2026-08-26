---
status: Accepted
date: 2026-08-26
impact: moderate
tags: [security, error-handling, mqtt]
---

# ADR-061: Decoupled Error-Message Disclosure

## Status

Accepted **Date:** 2026-08-26

## Context

ADR-011's 2026-07-24 amendment (LEAK-01) made `build_error_payload` default-deny: only exception types present in the `ErrorPublisher`'s `error_type_map` get their `str(error)` published, everything else is redacted to the class name. That amendment restored a consumer-facing `App(error_type_map=...)` opt-in hook so apps could register domain exception types back into full-message publishing (framework map entries stay authoritative — an app cannot override or shadow framework error handling).

The 2026-08 security audit (`docs/security/audit-report.md`) flagged this as finding F-DP1 (HARDENING, Med-Low, CWE-209/532): `error_type_map` conflates two independent concerns under one registration — assigning an `error_type` label for downstream consumers, and gating whether the raw exception message reaches broker-visible `{prefix}/error` and `{prefix}/{device}/error` topics. An app author who registers a domain exception purely to get a readable `error_type` (e.g. `caldav_connection_error` instead of the generic `error`) has no way to say 'label it, but keep the message redacted' — registration always does both. Because `error_type_map` matches by exact type only, this also means a message can leak the moment an app adds a label for classification, even when the underlying exception (e.g. a connection error wrapping a URL with embedded credentials, per ADR-011's own CalDav example) was never audited for message safety.

The existing `MqttSettings.error_publish_verbose` flag is a global, blunt escape hatch (discloses every exception's message process-wide) and does not address per-type labeling. The audit roadmap scheduled this as item #2 (structural, ADR-worthy, API change), following the same additive-on-0.x precedent ADR-060 established for F-DP5.

## Decision

Add an explicit, opt-in `disclose_messages_for: frozenset[type[Exception]] | None = None` parameter to `App.__init__`, `ErrorPublisher`, `build_error_payload()`, `AppHarness.create()`, and `create_services()`, because message disclosure and error-type labeling are independent security decisions that the existing `error_type_map` conflates (F-DP1). When `disclose_messages_for` is provided (not `None`), it **fully and independently** defines the disclosure policy: a type's `str(error)` is published only if that exact type is a member of the set, regardless of `error_type_map` membership — including framework-mapped types, which are not implicitly added to `disclose_messages_for` the way they are merged into `error_type_map` (`create_services` passes the app-supplied set through verbatim, unmerged). `None` (the default) preserves the legacy conflated behaviour for backward compatibility: `error_type_map` membership alone implies disclosure, exactly as ADR-011's LEAK-01 amendment specified. `verbose=True` (`MqttSettings.error_publish_verbose` / `App(error_publish_verbose=...)`) continues to override both and always discloses, unchanged from ADR-011.

This is additive and opt-in on the 0.x line — no existing app's behaviour changes unless it passes `disclose_messages_for=`. A future 1.0 ADR is expected to flip the default so `error_type_map` implies pure labeling only (no implicit disclosure), at which point disclosure will require an explicit `disclose_messages_for` set unconditionally.

```python
# Legacy conflated behaviour (disclose_messages_for=None, the default):
# registering a label also discloses the message.
app = cosalette.App(
    name="caldates2mqtt",
    error_type_map={CalDavConnectionError: "caldav_connection_error"},
)
# -> error_type="caldav_connection_error", message=str(error)  (disclosed)

# F-DP1 decoupled opt-in: label without disclosing.
app = cosalette.App(
    name="caldates2mqtt",
    error_type_map={CalDavConnectionError: "caldav_connection_error"},
    disclose_messages_for=frozenset(),  # explicit: disclose nothing
)
# -> error_type="caldav_connection_error", message="CalDavConnectionError"  (redacted)

# Label AND disclose, but only for audited-safe types:
app = cosalette.App(
    name="caldates2mqtt",
    error_type_map={
        CalDavConnectionError: "caldav_connection_error",
        CalDavNotFoundError: "caldav_not_found",
    },
    disclose_messages_for=frozenset({CalDavNotFoundError}),  # only this one is safe
)
```

## Decision Drivers

- F-DP1 (CWE-209/532, Med-Low): error_type_map conflates a labeling decision with a message-disclosure decision, so an app cannot register a readable error_type without also opting the exact type into message publication
- Backward compatibility on the 0.x line — existing apps that rely on ADR-011's LEAK-01 opt-in hook (jeelink2mqtt, vito2mqtt, caldates2mqtt) must not see a behaviour change unless they explicitly adopt the new parameter
- Least-privilege disclosure — an app should be able to say 'I audited this exception's message as safe' independently of 'I want this exception_type to have a readable label', since exact-type matching means even a domain exception can wrap secrets (e.g. URLs with embedded credentials)
- Consistency with the ADR-060 precedent: security-hardening changes to public error/publishing surfaces ship additively on 0.x with an explicit opt-in/opt-out, with the breaking default deferred to a documented 1.0 ADR
- verbose=True must remain the unconditional override for both the legacy and decoupled paths, since it is the existing blunt operator escape hatch documented in ADR-011 and must not silently stop working

## Considered Options

### Option 1: Status quo (error_type_map continues to imply disclosure)

Leave error_type_map as the single knob for both labeling and disclosure, as established by the ADR-011 LEAK-01 amendment; document the conflation as a known limitation and defer any fix to the 1.0 default flip.

- *Advantages:* Zero new API surface, zero migration or documentation burden this cycle; No behavioural change to reason about or test
- *Disadvantages:* Leaves F-DP1 open indefinitely with no interim mitigation before 1.0; Apps that want a readable error_type for an exception whose message safety is unverified have no way to get one without also disclosing the message; Audit finding stays unresolved for an unbounded number of 0.x releases

### Option 2: Decoupled disclose_messages_for opt-in set (chosen) (chosen)

Add an explicit disclose_messages_for frozenset parameter across App, ErrorPublisher, build_error_payload, AppHarness.create, and create_services. When provided it fully and independently defines disclosure; None preserves legacy behaviour. Framework map entries are not auto-merged into it.

- *Advantages:* Closes F-DP1 without any behaviour change for apps that do not opt in — fully additive on 0.x; Decouples labeling from disclosure so an app can register a label while keeping the message redacted, or vice versa within the constraints of error_type_map; Mirrors the ADR-060 precedent (explicit opt-in/opt-out param, legacy behaviour preserved, breaking default documented for 1.0) so the mental model is consistent across the security-hardening series; Unmerged framework pass-through keeps the security-relevant decision explicit per app — no silent framework-driven disclosure expansion
- *Disadvantages:* Two knobs (error_type_map, disclose_messages_for) must be reasoned about together until the 1.0 default flip, which is more surface to document and test; Apps that adopt disclose_messages_for must remember to re-list framework-mapped types if they want those messages disclosed, since the set is not auto-populated the way error_type_map is; Does not fully close F-DP1 on its own — the legacy conflated path remains the default until the 1.0 ADR ships

### Option 3: Flip the default now (breaking change on 0.x)

Immediately change error_type_map to imply pure labeling only, requiring every app that wants message disclosure to add disclose_messages_for explicitly, shipped as a 0.x minor/major bump.

- *Advantages:* Fully closes F-DP1 in one step with no interim two-knob period; Forces every app to make an explicit, audited disclosure decision per type immediately
- *Disadvantages:* Breaks every existing app relying on the ADR-011 LEAK-01 opt-in hook (jeelink2mqtt, vito2mqtt, caldates2mqtt) without a deprecation window; Contradicts the project's stated intent (audit roadmap, ADR-060 precedent) to reserve breaking defaults for 1.0; No way to distinguish 'app hasn't migrated yet' from 'app deliberately wants nothing disclosed' during the transition

## Decision Matrix

| Criterion | Status quo (error_type_map continues to imply disclosure) | Decoupled disclose_messages_for opt-in set (chosen) | Flip the default now (breaking change on 0.x) |
| --- | --- | --- | --- |
| Closes F-DP1 disclosure/labeling conflation | 1 | 4 | 5 |
| Backward compatibility for existing 0.x apps | 5 | 5 | 1 |
| Consistency with ADR-060 additive-hardening precedent | 2 | 5 | 2 |
| Least-privilege message disclosure achievable today | 1 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Apps can now register an error_type label for a domain exception without also opting its message into publication, closing the F-DP1 gap without waiting for a 1.0 release
- The disclosure decision is decoupled from the labeling decision and made per-type, so exact-type matching semantics stay unchanged while the security posture becomes strictly finer-grained
- Fully backward compatible — no existing app's published error messages change unless it explicitly passes disclose_messages_for
- Sets up a clean, already-documented migration path to the 1.0 ADR that will flip the default to pure labeling
- Framework map entries are not silently added to disclose_messages_for, so an app cannot be surprised by framework-driven message disclosure it did not request

### Negative

- Two knobs (error_type_map, disclose_messages_for) exist simultaneously until the 1.0 default flip, adding a documentation and mental-model burden for app authors and framework maintainers
- Apps adopting disclose_messages_for must remember to re-list any framework-mapped exception types they still want disclosed, since the set does not inherit error_type_map's framework-merge behaviour
- The legacy conflated behaviour (None) remains the default, so F-DP1 is mitigated rather than fully closed until the future 1.0 ADR ships

_2026-08-26_
