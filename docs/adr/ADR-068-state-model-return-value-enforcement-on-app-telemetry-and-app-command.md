---
status: Accepted
date: 2026-09-04
impact: high
tags: [serialization, error-handling, telemetry, release, architecture]
---

# ADR-068: state_model= Return-Value Enforcement on @app.telemetry and @app.command

## Status

Accepted **Date:** 2026-09-04

## Context

`cosalette ai help contracts` ships this rule verbatim (`_ai_content/_help_extra.py:397-401`): *"One rule for state_model: if you declare it, published state is validated. Since 0.6.0 that holds on every publishing archetype — @app.telemetry and @app.command validate the handler return value, @app.device and @app.stream validate every ctx.publish_state() payload."* The `@app.device` / `@app.stream` half is true. The `@app.telemetry` / `@app.command` half is not, and two independent mechanisms in `_runners/_contracts.py` defeat it:

1. **A return annotation displaces the explicit contract.** `normalize_handler_return` (`_contracts.py:444`) computes `annotation = get_return_annotation(func) or state_model` — so `state_model=` is consulted *only* when the handler has no resolvable return annotation. Every type-checked codebase annotates its handlers, and the idiomatic annotation for a heterogeneous payload is `dict[str, object]`, whose `TypeAdapter` accepts anything. The explicit `state_model=` never runs.
2. **The EAFP fast path swallows a mismatch.** `normalize_return` (`_contracts.py:292-296`) calls `adapter.dump_python(value, mode="json")` with no `warnings=`. Serialising a plain, non-conforming `dict` against a `BaseModel`/dataclass adapter emits `PydanticSerializationUnexpectedValue` as a `UserWarning` and returns the input unchanged; the `except Exception` → `validate_python` fallback never runs, so the non-conforming dict is published to the retained state topic verbatim. The hole is invisible to a test suite that only exercises hard failures (an unserialisable value *does* raise and fall through).

Downstream evidence (cosalette-apps pilot, `cap-b8h`): of the twelve `state_model=` declarations on value-returning telemetry/command handlers in the pilot monorepo, **ten are runtime no-ops**; the two that work do so by accident of style (they return a model *instance* annotated as that model). The declarations still drive schema generation and AsyncAPI, so nothing looks wrong from the outside — which is why the defect survived to 0.8.0.

This contradicts an accepted ADR. ADR-045's 2026-08-07 amendment justified installing *no* `state_model` on the telemetry/command `DeviceContext` on the premise that *"their state_model already validates the handler return value before publish_state is reached; re-validating the resulting JSON dict would check the same contract twice"* — the exact claim this ADR disproves. That amendment's own negative consequences predicted the trap: *"their differing dump/validate ordering is a subtlety future maintainers must respect."* Because ADR-034 / ADR-035 make `cosalette ai help` and the MCP layer the sanctioned context source for downstream apps and their AI agents, a false guarantee there is materially worse than an ordinary documentation bug: downstream teams are instructed to trust it.

The device/stream path is already correct: `validate_state_payload` (`_contracts.py:317-389`), added by the same ADR-045 amendment, always `validate_python`s first and is deliberately a separate function so the two orderings cannot be conflated. ADR-046 (the `TypeAdapter` validation engine) is aligned with the fix rather than in tension with it — it already frames *"the return annotation / state_model"* as a single contract source. The current released version is 0.8.0 (`pyproject.toml`); the project is in pilot and has no `1.0` milestone. Breaking defaults have shipped directly on 0.x minor boundaries before — ADR-045 (`state_model` on `@app.device`), ADR-060 (bounded handler execution), ADR-061 (error-message disclosure), ADR-062 (MQTT TLS default).

## Decision

Make an explicit `state_model=` a real return-value contract on `@app.telemetry` and `@app.command`, matching what `cosalette ai help contracts` already promises and what `@app.device` / `@app.stream` already do. Ship it as a direct breaking change in **0.9.0** — no two-phase detect/enforce, no `strict_state_models=` bridge flag — with a `BREAKING` CHANGELOG entry, citing ADR-045 / ADR-060 / ADR-061 / ADR-062 as the precedent for a breaking default on the 0.x line. Seven clauses:

**A. Precedence.** In `normalize_handler_return` (`_contracts.py:444`), change `annotation = get_return_annotation(func) or state_model` to `annotation = state_model or get_return_annotation(func)`. An explicit `state_model=` is an opt-in contract; a return annotation is frequently written only to satisfy a type checker. The explicit knob outranks the incidental one — aligning telemetry/command with device/stream (where a declared `state_model` is already the sole authoritative source) and with ADR-046's single-contract-source framing.

**B. Fail-closed serialisation.** The EAFP fast path in `normalize_return` becomes `adapter.dump_python(value, mode="json", warnings="error")`. `warnings="error"` turns `PydanticSerializationUnexpectedValue` into `PydanticSerializationError`, which the existing `except Exception:` already catches, so the existing `validate_python` fallback runs. A non-conforming plain `dict` then raises `ReturnValidationError`; the runner publishes it to `{prefix}/{name}/error` and suppresses the state publish, consistent with ADR-011 / ADR-061 error-topic disclosure semantics (message built only from framework-owned field-location and Pydantic error-type codes, never echoing the rejected payload — OWASP A03). The fast path stays free for genuine model instances: a real `BaseModel`, dataclass or `TypedDict` dumps with no warning and no extra work.

**C. Output shape under enforcement.** Validated models are serialised with `exclude_none=True`. This preserves the payload shape apps publish today and the conditional-key idiom — an absent optional field is an omitted key, not a `null`. Extra keys not on the model are still dropped; that is validation working and it is the one change that should be loud.

**D. Device/stream consistency.** `validate_state_payload` (`_contracts.py:317-389`) also adopts `exclude_none=True`, so the *one rule* has no archetype-dependent output shape. This changes the device/stream wire payload for handlers whose `state_model` has optional fields currently published as explicit `null`. The alternative — keeping device/stream byte-identical to 0.8.x and applying `exclude_none=True` on telemetry/command only — was considered and rejected: it reintroduces the per-archetype matrix ADR-045's amendment set out to kill the moment a handler is moved between archetypes.

**E. Delivery.** Direct breaking change in 0.9.0. No dry-run / detect phase, no `App(strict_state_models=...)` bridge flag: the project is in pilot, breaking changes to public API are acceptable at a 0.x minor boundary, and a permanent flag is disproportionate surface for a transient need. The evaluation's recommended two-phase rollout is recorded below as an alternative considered.

**F. Conflicting annotation.** When `state_model=M` and a return annotation name *different* types, emit a **WARNING at registration** (`@app.telemetry`, `@app.command`, and the `@router.*` / `App.add_*` equivalents) naming both types and stating that `state_model=` wins. Not a hard error — that would fail existing registrations at startup on upgrade. Same-type (`-> M` with `state_model=M`) is not a contradiction and stays silent.

**G. Dependency.** `warnings="error"` on `TypeAdapter.dump_python` needs nothing beyond the existing `pydantic>=2.12.5,<3` pin (`pyproject.toml`); it is available and stable across that range. Record it with a code comment; no version bump.

```python
# _runners/_contracts.py — normalize_handler_return
# BEFORE (clause A): a return annotation displaces the explicit contract
annotation = get_return_annotation(func) or state_model
# AFTER: the explicit contract outranks an incidental annotation
annotation = state_model or get_return_annotation(func)

# _runners/_contracts.py — normalize_return EAFP fast path
# BEFORE (clause B): a non-conforming plain dict is published unchanged (warning swallowed)
normalised = adapter.dump_python(value, mode="json")
# AFTER: serializer mismatch raises, is caught, and routes to validate_python
try:
    normalised = adapter.dump_python(value, mode="json", warnings="error")
except Exception:
    validated = adapter.validate_python(value)          # -> ReturnValidationError on failure
    normalised = adapter.dump_python(validated, mode="json", exclude_none=True)  # clause C
# clause D: validate_state_payload adopts exclude_none=True too — no archetype-dependent shape

@app.telemetry("reading", interval=30, state_model=Reading)   # Reading.brightness: int | None
async def read() -> dict[str, object]:
    return {"state": "ON"}          # brightness genuinely unknown
# 0.8.x: publishes {"state": "ON"} unvalidated — state_model= is inert
# 0.9.0: validated against Reading, publishes {"state": "ON"} (absent optional omitted, not null)
#        a non-conforming dict -> ReturnValidationError -> {prefix}/reading/error, no state publish
```

## Decision Drivers

- The `cosalette ai help contracts` guarantee (ADR-034 / ADR-035 sanctioned downstream context surface) and ADR-045's 2026-08-07 amendment both assert an invariant the code does not hold; a categorical false statement in the AI-context surface propagates into downstream app design and downstream AI agents.
- `state_model=` is named and documented as a contract but acts only as a no-annotation fallback on two of four archetypes — ten of twelve pilot declarations on value-returning telemetry/command handlers are runtime no-ops.
- Device and stream already validate-first via `validate_state_payload`; telemetry and command must converge on one rule rather than a per-archetype precedence matrix no ADR ever deliberately chose (ADR-046 single-contract-source framing).
- The EAFP `dump_python` fast path silently republishes a non-conforming plain dict because the Pydantic serializer warning is swallowed, so the defect is invisible to a test suite that only exercises hard serialisation failures.
- The project is in pilot with no `1.0` milestone; ADR-045 / ADR-060 / ADR-061 / ADR-062 establish that breaking defaults ship directly on a 0.x minor boundary, so a two-phase bridge and a permanent opt-in flag are unwarranted overhead.
- Hot-path serialisation cost must stay bounded (ADR-013 / ADR-021): the fast path must remain allocation- and validation-free for values that are already valid model instances.

## Considered Options

### Option 1: Explicit state_model= wins + fail-closed serialisation, direct break at 0.9.0 (chosen)

Clauses A–G above: explicit `state_model=` outranks the return annotation; the EAFP fast path uses `warnings="error"` so a non-conforming plain dict falls through to `validate_python` and, on failure, raises `ReturnValidationError` published to `{prefix}/{name}/error` with the state publish suppressed; validated models serialise with `exclude_none=True` on both `normalize_return` and `validate_state_payload`; a registration-time WARNING fires when `state_model=` and a differently-typed return annotation disagree. Shipped directly in 0.9.0 with a `BREAKING` CHANGELOG entry.

- *Advantages:* `state_model=` finally behaves as `cosalette ai help contracts` and the guides already say it does — the AI-context surface stops lying to downstream teams; One rule across all four publishing archetypes, with no archetype-dependent output shape (clause D); A non-conforming plain dict — the dominant return shape — can no longer publish silently to a retained topic; ~3 lines of production change plus a registration warning; the fast path stays free for genuine model instances; Matches the established 0.x breaking-default precedent (ADR-045/060/061/062); no permanent flag surface, no two-release coordination
- *Disadvantages:* Breaking on upgrade: handlers whose payloads never matched their declared model start raising `ReturnValidationError` on first boot after 0.9.0 — most likely a missing required field; `exclude_none=True` on `validate_state_payload` changes the device/stream wire payload for `state_model`s with optional fields currently published as explicit `null`, contrary to the byte-stability expectation set around ADR-045's amendment; `warnings="error"` also promotes other Pydantic serializer warnings (lossy coercions, dropped keys), pushing more cases through `validate_python`; needs a byte-identical negative-control test as a guardrail; No dry-run: downstream teams get their migration list as a boot failure rather than a warning log first; Couples framework behaviour to Pydantic's serializer-warning surface, which a v3 migration could reshape

### Option 2: Documentation-only correction

Leave the code as-is. Correct `_ai_content/_help_extra.py`, `docs/guides/contract-first-route-design.md`, and `docs/reference/cosalette-framework-reference.instructions.md` to describe the real precedence (return annotation first, `state_model=` only as a no-annotation fallback) and the plain-dict bypass. `state_model=` remains schema/AsyncAPI metadata on telemetry/command.

- *Advantages:* Cheapest possible action; zero behaviour change and zero migration risk; Still needed regardless of the code decision — the shipped text is false today; No hot-path change
- *Disadvantages:* `state_model=` keeps a name that says "contract" while doing nothing at runtime on two of four archetypes — a misleading spelling for schema metadata; Leaves the per-archetype precedence matrix in place; a handler moved from `@app.device` to `@app.telemetry` silently loses validation; Does not close the silent-publish hole for non-conforming dicts; Contradicts ADR-046's single-contract-source framing

### Option 3: Precedence fix only (clause A)

Apply `annotation = state_model or get_return_annotation(func)` but leave the EAFP `dump_python` fast path unchanged (no `warnings="error"`).

- *Advantages:* One-line change; closes the common `-> dict[str, object]` case where the annotation is loose; No new warning-surface behaviour to reason about; Fast path unchanged, so hot-path cost is identical to today
- *Disadvantages:* A handler annotated `-> Model` that returns a plain `dict` is still not validated — the fast path passes the non-conforming dict straight through; The rule still has an exception nobody can predict; `state_model=` enforcement remains conditional on return shape; Ships a half-fix that will need a second breaking change later to close the serialisation hole

### Option 4: Two-phase detect-then-enforce rollout

Ship clauses A+B in a dry-run mode at one 0.x minor: on mismatch, log once per registration per boot at WARNING naming handler, model, offending field paths and the enforcing release, then publish exactly what is published today. Flip to raising `ReturnValidationError` one minor later. Optionally add an `App(strict_state_models=...)` bridge flag for teams that want enforcement immediately.

- *Advantages:* Every downstream gets its migration list from one boot with no behaviour change and nothing to opt into; Lowest blast radius — the enforcing release lands only after the pilot has reported its dry-run findings; A model wrong since it was written is surfaced before it can fail a deployment
- *Disadvantages:* Once-per-registration dedup plumbing in both the telemetry and command runners, a stable warning string, and two coordinated releases; `strict_state_models=` is permanent-ish public surface for a transient need; the documented rule is unconditional and the implementation should be too; Disproportionate for a pilot-stage project whose breaking-default precedent (ADR-045/060/061/062) is a direct flip on a 0.x minor; Delays contract honesty by a full minor cycle

### Option 5: Validate against state_model, serialise via the return annotation

When both are present, `validate_python` the return value against `state_model=` but `dump_python` it through the return annotation, so the wire shape follows the annotation while the contract check follows the model.

- *Advantages:* Both declarations retain a runtime role; Wire payload shape is unchanged wherever the annotation is `dict[str, object]`
- *Disadvantages:* Two adapter passes per publish cycle on the hot path, to model a disagreement that should be a registration-time concern (clause F); Encodes a contradiction rather than resolving it; the effective contract is neither declaration; Diverges further from the device/stream path instead of converging on it; Hardest of the options to explain in one sentence of documentation

## Decision Matrix

| Criterion | Explicit state_model= wins + fail-closed serialisation, direct break at 0.9.0 | Documentation-only correction | Precedence fix only (clause A) | Two-phase detect-then-enforce rollout | Validate against state_model, serialise via the return annotation |
| --- | --- | --- | --- | --- | --- |
| Contract honesty (state_model= behaves as documented) | 5 | 1 | 3 | 4 | 3 |
| Cross-archetype consistency (one rule, one output shape) | 5 | 2 | 3 | 5 | 2 |
| Fail-closed correctness (non-conforming plain dict cannot publish silently) | 5 | 1 | 2 | 5 | 4 |
| Migration safety (blast radius on upgrade) | 3 | 5 | 3 | 5 | 2 |
| Hot-path cost (ADR-013 / ADR-021) | 4 | 5 | 4 | 4 | 2 |
| Implementation & maintenance surface | 4 | 5 | 5 | 2 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- `state_model=` on `@app.telemetry` and `@app.command` validates the handler return value, so the `cosalette ai help contracts` guarantee, `docs/guides/contract-first-route-design.md`, and `docs/reference/cosalette-framework-reference.instructions.md` become true statements (ADR-034 / ADR-035 context integrity).
- One rule across all four publishing archetypes with a single output shape — declare `state_model` and published state is validated and serialised with `exclude_none=True`, whichever decorator is used.
- A non-conforming plain `dict` return raises `ReturnValidationError` and is published to `{prefix}/{name}/error` with the state publish suppressed, instead of reaching a retained topic and propagating to every subscriber.
- The conditional-key idiom stops needing a workaround: absent optional fields are omitted, not null-filled, so retained payloads and Home Assistant `value_template`s keep working (wiz2mqtt's `bulb_entity` no longer needs to refuse `state_model=`).
- `normalize_return` and `validate_state_payload` converge on validate-first semantics, closing the dump/validate ordering divergence ADR-045's amendment flagged as a hazard for future maintainers.
- A registration-time WARNING makes a `state_model=` / return-annotation type disagreement visible at startup rather than resolving it silently.

### Negative

- Breaking change in 0.9.0: telemetry/command handlers whose payloads never matched their declared `state_model` now raise `ReturnValidationError` on first boot — most often a missing required field. Migration is one of two one-line choices: fix the payload, or drop `state_model=` to return to unvalidated publishing. CHANGELOG carries a `BREAKING` entry.
- `exclude_none=True` on `validate_state_payload` (clause D) changes the device/stream wire payload for any `state_model` with optional fields currently published as explicit `null` — a key that was `null` becomes absent. This is contrary to the byte-stability expectation around ADR-045's amendment and should be confirmed by the maintainer before acceptance.
- `warnings="error"` also promotes unrelated Pydantic serializer warnings (lossy coercions, keys dropped as not-on-model) to the `validate_python` fallback, which can reshape a payload that previously dumped silently; a conforming-dict byte-identical negative-control test is required as a guardrail.
- No dry-run phase: downstream teams discover non-conforming handlers as a boot-time error rather than a prior warning log, so the 0.9.0 upgrade needs migration notes referencing the Phase-0 documentation correction.
- Framework behaviour is coupled to Pydantic's serializer-warning surface; a Pydantic v3 migration could change which warnings `warnings="error"` promotes (tracked at the `_get_adapter` layer, per ADR-046).
- A machine-readable `state_model` drift topic (e.g. `{prefix}/_meta/state_model_drift`) for fleet-wide scraping is NOT part of this decision and is deferred to a follow-up ADR.

_2026-09-04_
