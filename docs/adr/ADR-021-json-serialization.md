# ADR-021: JSON Serialisation

## Status

Accepted **Date:** 2026-03-07

## Context

Cosalette serialises JSON in several hot paths: telemetry state publication
(`DeviceContext.publish_state`), error payloads, heartbeat payloads, structured
logging, and persistence stores. All call sites used the standard library `json`
module directly, scattering `import json` / `json.dumps` / `json.loads` across
five internal modules.

This created two problems:

1. **No single choke-point.** Swapping the serialisation backend (e.g. for
   performance or feature differences) required touching every file
   independently.
2. **Performance ceiling.** The stdlib `json` module is pure Python and
   significantly slower than compiled alternatives — relevant for
   high-frequency telemetry publication.

The original framework evaluation document (P4 — Performance & Polish)
suggested adding orjson as an *optional* dependency under a `cosalette[fast]`
extra. However, cosalette already depends on compiled C extensions (pydantic,
pydantic-settings) and targets Python ≥ 3.14 only, so the portability argument
for keeping it optional is weak. A hard dependency is simpler: one code path,
no conditional imports, no feature-flag complexity.

## Decision

Adopt **orjson ≥ 3.10** as a hard (non-optional) dependency and route all JSON
serialisation through a private `cosalette._json` module.

The module exposes three functions and one re-exported exception:

| Symbol | Purpose |
| --- | --- |
| `dumps(obj, *, default=None) -> str` | Serialise to compact JSON string |
| `dumps_pretty(obj) -> str` | Serialise with 2-space indentation (stores) |
| `loads(data) -> Any` | Deserialise JSON string or bytes |
| `JSONDecodeError` | Re-exported from orjson; subclass of `json.JSONDecodeError` |

`dumps()` decodes orjson's `bytes` output to `str` because every call site in
cosalette (MQTT publish, `to_json()` methods, log formatters) expects a string.

### Migration scope

Five internal modules were migrated:

| Module | Calls replaced |
| --- | --- |
| `_context.py` | `json.dumps` → `dumps` |
| `_errors.py` | `json.dumps` → `dumps` |
| `_health.py` | `json.dumps` → `dumps` |
| `_logging.py` | `json.dumps(…, default=str)` → `dumps(…, default=str)` |
| `_stores.py` | `json.loads` → `loads`, `json.dumps(…, indent=2)` → `dumps_pretty`, `json.JSONDecodeError` → `JSONDecodeError` |

Test files were **not** migrated — they use `json.loads()` only for assertion
verification, not framework behaviour.

## Decision Drivers

- **Single choke-point** — all JSON goes through one module; backend swaps are
  one-file changes.
- **Performance** — orjson is 3–10× faster than stdlib `json` for typical
  payloads (benchmarked by the orjson project).
- **Simplicity** — hard dependency eliminates conditional imports, feature
  flags, and dual code paths.
- **Existing precedent** — cosalette already depends on compiled extensions
  (pydantic).

## Considered Options

### Option A: Hard dependency with wrapper module (Chosen)

Add `orjson>=3.10` to `dependencies`. Create `cosalette._json` as a thin
facade.

- *Advantages:* Consistent fast JSON everywhere. Single import pattern. Zero
  conditional logic. Easy to swap backend later.
- *Disadvantages:* Adds a compiled C-extension dependency. Slightly narrows
  platform support (orjson does not publish wheels for every platform).

### Option B: Optional dependency with runtime fallback

Add orjson as an extra (`cosalette[fast]`). Fall back to stdlib `json` when
orjson is absent.

- *Advantages:* Works without orjson. Wider platform compatibility.
- *Disadvantages:* Two code paths to test. Conditional imports in every module.
  Violates "one obvious way to do it". Performance is opt-in rather than
  guaranteed.

### Option C: Keep stdlib `json`

Do nothing. Accept the performance ceiling.

- *Advantages:* Zero new dependencies.
- *Disadvantages:* Scattered `import json` across modules. No centralised
  control. Slower serialisation on telemetry hot paths.

## Decision Matrix

| Criterion | A: Hard + wrapper | B: Optional | C: Keep stdlib |
| --- | :-: | :-: | :-: |
| Performance | 5 | 4 | 2 |
| Simplicity | 5 | 2 | 4 |
| Maintainability | 5 | 3 | 3 |
| Platform reach | 3 | 4 | 5 |
| Swap-path clarity | 5 | 3 | 2 |
| **Total** | **23** | **16** | **16** |

*Scale: 1 (poor) to 5 (excellent)*

## Consequences

### Positive

- All JSON serialisation is centralised in `_json.py` — backend changes are
  localised to one file.
- Telemetry publishing, error reporting, and logging benefit from orjson's
  compiled performance without per-site opt-in.
- `JSONDecodeError` re-export preserves backward compatibility — existing
  `except json.JSONDecodeError` handlers catch it because `orjson.JSONDecodeError`
  is a subclass of `json.JSONDecodeError`.
- `dumps_pretty()` produces structurally equivalent, 2-space-indented output
  to `json.dumps(indent=2)` for ASCII payloads (validated by test). Non-ASCII
  characters are emitted as raw UTF-8 rather than `\uXXXX` escape sequences —
  this is spec-compliant (RFC 8259 §8.1) and the modern default.

### Negative

- Platforms without orjson wheels require building from source (Rust toolchain
  needed). Mitigated: cosalette targets Linux/macOS on x86-64 and ARM64, all
  of which have pre-built wheels.
- orjson's `default` parameter semantics differ subtly from stdlib — the
  function is called once (no recursion). Mitigated: the only use
  (`default=str`) returns a natively-serialisable type, so no recursion is
  needed.
- orjson produces compact JSON (`{"key":"value"}` without spaces). Test
  assertions that compared raw JSON strings needed updating to use structural
  (`json.loads` + dict comparison) assertions instead.

_2026-03-07_
