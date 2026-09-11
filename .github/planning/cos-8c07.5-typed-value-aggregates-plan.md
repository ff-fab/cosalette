# cos-8c07.5 — Typed value aggregates on array-valued properties

**Status:** Implemented (B1 + E confirmed) · branch `feature/typed-value-aggregates`
**Decision of record:** [ADR-076](../../docs/adr/ADR-076-typed-value-aggregates-on-array-valued-properties-for-cross-target-consumer-parity.md)
**Epic:** cos-8c07 (cross-target consumer generation parity)

## Goal

Give an array-valued property a **single value** via a typed `aggregate`
declaration so it flows through the per-property machinery *both* generators
already implement — dissolving the root cause the ADR-059 discovery gate really
complains about (an array has no single value), rather than routing around the
symptom (HA-only `ha_entities()` composites).

```python
events: list[Event] = Field(
    json_schema_extra=consumer(display_name="Upcoming events", aggregate="count")
)
```

One declaration, two renderings:

| Target | Rendering |
| --- | --- |
| openHAB | `transformationPattern="JSONPATH:$.events.length()"` |
| Home Assistant | `value_template: {{ value_json.events \| length }}` |

## Implementation surface (verified in tree)

| Concern | Site |
| --- | --- |
| Producer TypedDict | `ConsumerMeta` — `_schema/__init__.py:79` |
| Producer function | `consumer()` — `_schema/__init__.py:96` |
| Reader dataclass | `ConsumerMetadata` — `_schema/__init__.py:45` |
| Loader (parses `x-cosalette-consumer`) | `_schema/_loader.py` |
| HA render | `_derive_value_template` — `_consumer_gen.py:449` |
| openHAB render | `_channel_entries` — `_consumer_gen.py:1227` (JSONPATH site) |
| Emittability gate | `_is_emittable` / `_is_array_of_objects` — `_consumer_gen.py:610` |
| Drift-guard test | producer-keys ⇄ reader-fields parity test |

## Open decisions

### Decision A — the aggregate enum and where it lives

`Aggregate = Literal["count", "min", "max", "avg", "sum"]` defined once in
`_schema/__init__.py`, referenced by both `ConsumerMeta` (producer, keys-only)
and `ConsumerMetadata` (reader). `count` is primary; `min`/`max`/`avg`/`sum`
are the numeric family.

- **Recommended:** single `Aggregate` alias, closed `Literal`. Producer stays
  keys-only (current convention — no value-enum validation in `consumer()`);
  the enum is enforced at **generation time** where the type context exists.

### Decision B — `avg` renders differently per target (the real divergence)

`count`, `min`, `max`, `sum` exist natively on **both** sides:

| Aggregate | openHAB (Jayway) | HA (Jinja filter) |
| --- | --- | --- |
| count | `$.x.length()` | `{{ value_json.x \| length }}` |
| min | `$.x.min()` | `{{ value_json.x \| min }}` |
| max | `$.x.max()` | `{{ value_json.x \| max }}` |
| sum | `$.x.sum()` | `{{ value_json.x \| sum }}` |
| **avg** | `$.x.avg()` | **no `avg` filter** |

Jinja has no `avg` filter, so `avg` must render as
`{{ (value_json.x | sum) / (value_json.x | length) }}` on HA while openHAB uses
native `avg()`.

- **Option B1 (recommended):** implement the full family now; special-case
  `avg` on the HA side with the `sum / length` form. One extra branch, satisfies
  AC #1–#5 as written.
- **Option B2:** ship `count` only this cycle, defer `min/max/avg/sum` to a
  follow-up. Smaller, but leaves AC #5 (type-invalid rejection) partly moot and
  splits the feature.

### Decision C — validity per item type (drives AC #5)

- `count` is valid on **any** array (including array-of-objects — it counts
  elements).
- `min/max/avg/sum` require an array of **numbers**; on a non-numeric array they
  are rejected **at generation time** with a named error (`SchemaError`/
  `ValueError`), never emitted to fail at deployment.

### Decision D — emittability & the ADR-059 gate

An array-valued property carrying a valid `aggregate` becomes **emittable**, so
the channel passes the discovery gate. `_is_array_of_objects` currently forces
"not emittable"; the aggregate is the single value that flips it. `count` is the
only aggregate that makes an *array-of-objects* emittable; the numeric family
only applies to arrays of numbers (Decision C).

### Decision E — component / unit for a plain count

Leave `unit` **unset** for a bare count so openHAB resolves to `DecimalType`
(not `QuantityType`) and HA renders a plain `sensor`. Author may still add
`unit=` / `device_class=` / `state_class=` via `consumer()` as today. No implicit
`state_class: measurement`.

### Decision F — aggregates are state-only

An aggregate is an observation; it is never a command target. Force the
property state-only (like `read_only`) regardless of channel direction, so no
`command_topic`/`commandTopic` is emitted for an aggregated property.

### Decision G — explicit overrides still win

`_derive_value_template` already returns `ha.value_template` first, so an
explicit `ha_discovery(value_template=...)` keeps winning over the derived
aggregate template (AC #7) with no extra work. The openHAB `channel_params`
escape hatch likewise still overrides in place.

## Proposed work breakdown

1. **Types** — add `Aggregate` alias; `aggregate` key on `ConsumerMeta`,
   `aggregate` field on `ConsumerMetadata`; loader parses it.
2. **Emittability** — array + valid aggregate ⇒ emittable (`_is_emittable`,
   silence classification stays correct).
3. **HA render** — `_derive_value_template` renders the aggregate (special-case
   `avg`).
4. **openHAB render** — `_channel_entries` appends `.<fn>()` to the JSONPATH
   selector; state-only.
5. **Validation** — generation-time rejection of numeric aggregates over
   non-numeric arrays, with a named error (AC #5).
6. **Tests** — adopter round-trip in both targets (AC #1); byte-identical
   no-aggregate output (AC #2); colon-form `length()` pin (AC #3); unit-unset /
   DecimalType (AC #4); invalid-aggregate rejection (AC #5); override precedence
   (AC #7); drift-guard parity.
7. **Docs & AI content** (per workflow feature checklist) — missing-key/
   stale-value semantics (AC #6) in `docs/`, the guidance asset, and `ai help`;
   `ai prime` what's-new entry; update scaffolding templates if the registration
   API surface changes.

## Key questions for you

1. **Decision B:** full `count/min/max/avg/sum` family now (B1, recommended), or
   `count`-only this cycle (B2)?
2. **Decision E:** agree with leaving `unit`/`state_class` unset for a bare
   count (no implicit `measurement`)?
3. Anything to add to the author surface (e.g. an explicit `aggregate` producer
   keyword vs. only through `consumer(aggregate=...)`)?
