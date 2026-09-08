---
status: Accepted
date: 2026-09-08
impact: high
tags: [mqtt, architecture, configuration, devices, cli]
---

# ADR-072: Prefix-Aware AsyncAPI Generation and the Identity-vs-Address Split

## Status

Accepted **Date:** 2026-09-08

## Context

`docs/reference/mqtt-topics.md:100-107` states the contract plainly: the `{prefix}` placeholder resolves from `Settings.mqtt.topic_prefix` when set, and falls back to `App(name=...)` otherwise. The runtime honours this — `_app/_lifecycle.py:193` computes `prefix = resolved_settings.mqtt.topic_prefix or self._name` and threads it into the MQTT client and the router. The **schema pipeline does not**.

`_schema/_asyncapi.py:325-339` `_build_mqtt_address(app_name, reg_name, address_suffix, *, is_root)` composes `f"{app_name}/{reg_name}/{address_suffix}"`. No prefix parameter exists anywhere in the call graph: `build_app_asyncapi(app)` (`:711`) passes `app.name` at `:762`, `:779` and `:797`, and `App.asyncapi()` (`_app/_asyncapi.py:14`) takes no settings at all, caching the single result into `_asyncapi_cache`. Even the ADR-051 settings-resolving pipeline drops the value on the floor: `_schema/_cli_helpers.py:165` `_resolve_app_settings()` constructs the resolved `settings` object at `:226` and then returns only `app` at `:286`.

The result is that every artefact derived from the AsyncAPI document describes topics a prefixed app never touches:

1. `schema dump` / `check` / `init` emit the wrong `channel.address`.
2. `schema acl` grants the wrong topic patterns — the broker then denies every publish, so the failure is at least fail-closed.
3. `schema ha-discovery` and `schema openhab` read `channel.address` verbatim (`_schema/_consumer_gen.py:447-452`, `:674-676`, `:1015`) and emit unreachable `state_topic` / `command_topic` values.
4. The runtime `_meta/registry` payload disagrees with the topic it is published on (`_wiring/_infra.py:279`, `:305`).
5. **The worst manifestation is runtime, not CLI.** Under ADR-059, `_wiring/_discovery.py:98` `build_discovery_payloads()` calls `app.asyncapi()` and publishes **retained** Home Assistant discovery. A live prefixed app using `App.discovery()` therefore permanently advertises topics it never writes; every entity shows as unavailable in Home Assistant, and no CLI command is involved in producing that state.

The same conflation breaks in the **opposite direction** on the enforcement path. `_app/_lifecycle.py:255-257` passes the *address* prefix into `load_and_validate_schema(..., prefix)`, which reaches `_schema/_enforcement.py:143` `registry.filter_for_app(prefix)`. That filter (`_schema/__init__.py:382-391`) matches on `channel.app_name` — the `x-cosalette-app` tag established by ADR-033, which is an **identity**, not a topic. Verified against a real registry: `filter_for_app('house/wiz')` returns `[]` while `filter_for_app('wiz2mqtt')` returns `['deskState']`. A prefixed app with `x-cosalette-enforcement.network_level: true` therefore filters its own slice down to zero channels, and in `strict` mode refuses to start.

The naive fix — prepend the prefix wherever the app name is used today — is itself breaking. `topic_prefix` accepts **multi-segment** values: the validator at `_settings/__init__.py:164-172` rejects only `+`, `#` and NUL and strips outer slashes, and the router handles multi-segment prefixes at `_mqtt/_router.py:264-266`. But the address parsers assume exactly one leading segment. `_schema/__init__.py:478-494` `_device_name_from_archetype` strips exactly one segment, so under prefix `house/wiz` the address `house/wiz/desk/state` yields device name `"wiz/desk"` instead of `"desk"`. That flows into `_resolve_device` (`_schema/_consumer_gen.py:113-129`) and changes the Home Assistant `object_id` and `unique_id`, orphaning every entity a user already has. `registry.device_names` (`:497-511`) breaks `_validate_registrations` the same way.

Relation to prior decisions:

- **ADR-002** (MQTT topic conventions) defines `{prefix}/{device}/{signal}`; the prefix has always been part of that structure, and this ADR does not change it.
- **ADR-033** (MQTT schema enforcement) introduced `x-cosalette-app` as the app-ownership tag. It is an identity marker; nothing in ADR-033 licenses using it as a topic segment, and nothing licenses using a topic segment to look it up.
- **ADR-051** (settings-aware schema pipeline) is the decision that made resolved settings available to schema generation in the first place. Its Editorial notes (2026-08-05, 2026-08-08) carefully enumerate what the pipeline deliberately does *not* resolve — `resolve_intervals`, `resolve_timeouts`, `resolve_intervals_periodic`, the `Store`, and non-dry-run adapters. `topic_prefix` is **not** in that list. It is an oversight, not a documented exclusion.
- **ADR-059** (runtime Home Assistant discovery) is what turns this from a CLI-output defect into a live, retained-state defect.

An additional constraint on any fix: consumers of a **serialised** document are detached from the `App` object. `schema acl`, `schema ha-discovery`, `schema openhab` and the downstream compliance monitor read a `.json` file. If the prefix lives only as a Python call parameter, those consumers cannot recover it and can only guess `app_name`.

## Decision

Thread the resolved topic prefix through AsyncAPI generation as a parameter distinct from the app name, **and** record it in the generated document as an additive info-level `x-cosalette-topic-prefix` extension, because a serialised document must be self-describing for the detached consumers that read it back. Rule explicitly that `x-cosalette-app` is **identity** and `channel.address` is **transport**, and that the two are never interchangeable.

Concretely:

- `_build_mqtt_address` takes the prefix separately from `app_name`; `build_app_asyncapi(app, *, topic_prefix=None)` defaults it to `app.name`; `App.asyncapi(topic_prefix=...)` keys its cache by the prefix; `_resolve_app_settings` returns the resolved prefix alongside the app.
- `x-cosalette-topic-prefix` is emitted in the `info` section, next to the existing `x-cosalette-contract-version`. The loader performs no unknown-extension rejection (`_schema/_loader_helpers.py:30-115`), so documents carrying the new key load in older versions and documents lacking it load in newer ones — where the prefix falls back to `app_name`, which is exactly today's behaviour.
- Address parsers become prefix-length aware: they strip `len(prefix.split("/"))` leading segments instead of exactly one. Device names, and therefore Home Assistant `object_id` and `unique_id`, stay **stable** across no prefix, a single-segment prefix and a multi-segment prefix. This is a hard acceptance criterion, not an aspiration.
- `_app/_lifecycle.py:255-257` passes `self._name` (identity) to `filter_for_app`, while `build_skip_topics(prefix, ...)` keeps the address.
- `_wiring/_infra.py:279` and `_wiring/_discovery.py:98` receive the prefix the runtime already holds.
- A `--topic-prefix` flag on the schema commands complements `--resolve-settings` for CI gates that must not run configure hooks.

Apps that set no `topic_prefix` are byte-identical to today, by definition: the prefix equals `app.name`.

```python
# Identity and transport are separate inputs, never the same value.
def _build_mqtt_address(
    topic_prefix: str,      # TRANSPORT: settings.mqtt.topic_prefix or app.name
    reg_name: str,
    address_suffix: str,
    *,
    is_root: bool,
) -> str: ...


def build_app_asyncapi(app: App, *, topic_prefix: str | None = None) -> dict[str, Any]:
    prefix = topic_prefix or app.name
    # channel.address  -> composed from `prefix`   (transport)
    # x-cosalette-app  -> always `app.name`        (identity)
    # info.x-cosalette-topic-prefix -> `prefix`    (so the document is self-describing)
    ...


# Enforcement filters on IDENTITY, skip-topics operate on the ADDRESS.
schema_registry = await _schema_enforcement.load_and_validate_schema(
    self.registered_names, resolved_settings, self._name  # identity, not prefix
)
skip_topics = build_skip_topics(prefix, ...)              # address, not identity
```

## Decision Drivers

- The documented `{prefix}` contract in `docs/reference/mqtt-topics.md:100-107` is violated by every AsyncAPI-derived artefact, so generated ACLs, HA discovery and openHAB output all point at topics a prefixed app never uses.
- Runtime Home Assistant discovery (ADR-059) publishes RETAINED payloads from `app.asyncapi()`, so the defect leaves persistent broker state that outlives the process and requires manual cleanup.
- Detached consumers read a serialised `.json` document with no access to the `App` object, so a call-parameter-only fix leaves `schema acl`, `schema ha-discovery`, `schema openhab` and the compliance monitor unable to recover the prefix.
- Home Assistant `unique_id` stability is non-negotiable: any change to the derived device name orphans entities users already have, converting a bug fix into a migration.
- `topic_prefix` legitimately accepts multi-segment values, so any parser that assumes exactly one leading segment is wrong for a supported configuration.
- Enforcement currently fails in the opposite direction — an address is passed where an identity is expected — so fixing generation alone would still leave `network_level` enforcement broken for prefixed apps.
- Existing committed schema documents and unprefixed apps must keep working with byte-identical output, or the fix cannot ship in a 0.x patch.

## Considered Options

### Option 1: Thread the prefix as a call parameter only

Add a `topic_prefix` parameter to `_build_mqtt_address`, `build_app_asyncapi` and `App.asyncapi`, have `_resolve_app_settings` return the resolved prefix, and pass the runtime prefix at the `_wiring` call sites. The generated document gains correct addresses but records nothing about how they were composed.

- *Advantages:* Smallest change set; no new document surface to version or document.; Fixes `schema dump`/`check`/`init`, the `_meta/registry` payload and runtime HA discovery, which are the paths that hold an `App` object.; Generated documents remain byte-identical for unprefixed apps with no extension key to explain.
- *Disadvantages:* Detached consumers of a serialised document cannot recover the prefix and must guess `app_name`, so `schema acl`, `schema ha-discovery` and `schema openhab` stay wrong whenever they read a file rather than an app.; Prefix-length-aware address parsing is impossible without the prefix, so multi-segment prefixes still corrupt device names and Home Assistant `unique_id`s.; The downstream compliance monitor has no way to distinguish a prefixed document from an app that simply happens to be named `house/wiz`.

### Option 2: Parameter plus an additive x-cosalette-topic-prefix info extension (chosen)

Everything in the parameter-only option, plus an additive `x-cosalette-topic-prefix` key in the document's `info` section alongside the existing `x-cosalette-contract-version`. The loader surfaces it on `SchemaRegistry`; when absent it falls back to `app_name`. Address parsers then strip `len(prefix.split("/"))` segments, so device names stay stable for any prefix depth. Enforcement is corrected to filter on `x-cosalette-app` identity while skip-topics keep using the address.

- *Advantages:* The serialised document becomes self-describing, so every detached consumer — `schema acl`, `schema ha-discovery`, `schema openhab`, the compliance monitor — resolves the correct prefix without re-importing the app.; Prefix-length awareness becomes possible, which is the only way `object_id`/`unique_id` can stay stable under a multi-segment prefix.; Fully backward compatible in both directions: the loader rejects no unknown extensions (`_loader_helpers.py:30-115`), and an absent key falls back to `app_name` — today's behaviour, byte for byte, for every existing document.; Follows an established precedent in the same file (`x-cosalette-contract-version`), so it adds no new mechanism, only a new key.; Fixes the retained runtime discovery path and the `network_level` enforcement path in the same change.
- *Disadvantages:* Adds a key to the AsyncAPI document that must be documented, tested for round-trip fidelity and maintained as part of the contract surface.; Largest diff of the four options: generation, loader, registry, address parsing, ACL, both consumer generators, three runtime call sites and the CLI.; A document hand-written by a user who omits the key silently gets `app_name` semantics, which is correct but not obvious without reading the docs.

### Option 3: Make the address the sole source of truth and re-derive identity from it

Drop the identity/transport distinction instead of sharpening it: treat `channel.address` as authoritative and derive app ownership by parsing its leading segment, rather than reading `x-cosalette-app`. `filter_for_app` would then match an address prefix, which is what `_app/_lifecycle.py:255-257` already assumes it does.

- *Advantages:* Removes one of the two concepts, so there is no possibility of them disagreeing.; Makes the existing `_app/_lifecycle.py:255-257` call site correct as written, with no change at that line.; Requires no new document surface.
- *Disadvantages:* Directly contradicts ADR-033, which established `x-cosalette-app` as the app-ownership tag that survives regeneration and channel renaming.; Ambiguous by construction: two apps deployed under the same `topic_prefix` — a supported and common staging pattern — become indistinguishable, and their enforcement slices merge.; Multi-segment prefixes make the leading-segment parse ambiguous with nested device paths, so ownership resolution becomes guesswork.; Identity would become mutable by configuration: changing `MQTT__TOPIC_PREFIX` would silently change which app a channel belongs to.

### Option 4: A --topic-prefix CLI flag alone

Leave the generation internals as they are and expose a `--topic-prefix` flag on the schema commands that rewrites addresses after the fact, or is passed straight down to composition. The prefix is supplied by the operator or the CI pipeline rather than resolved from settings.

- *Advantages:* Very small diff and no change to the document format.; Useful to CI gates that must not run configure hooks or load an env file, so it earns its place as a complement.; No risk to unprefixed apps at all — the flag is opt-in.
- *Disadvantages:* Does nothing for the runtime paths: `_wiring/_discovery.py:98` and `_wiring/_infra.py:279` never see a CLI flag, so retained HA discovery for a live prefixed app stays broken — the single worst symptom.; Does not fix `network_level` enforcement, which is an in-process identity lookup with no CLI involved.; Makes correctness opt-in and operator-remembered rather than derived from the settings that are already resolved.; Still leaves multi-segment prefixes corrupting device names, since nothing teaches the parsers the prefix depth.

## Decision Matrix

| Criterion | Thread the prefix as a call parameter only | Parameter plus an additive x-cosalette-topic-prefix info extension | Make the address the sole source of truth and re-derive identity from it | A --topic-prefix CLI flag alone |
| --- | --- | --- | --- | --- |
| Correct addresses for detached consumers of a serialised document | 2 | 5 | 4 | 2 |
| Home Assistant unique_id / object_id stability | 2 | 5 | 2 | 1 |
| Backward compatibility of existing committed documents | 5 | 5 | 1 | 5 |
| Fixes the retained runtime HA discovery path (ADR-059) | 5 | 5 | 3 | 1 |
| Fixes network-level enforcement filtering | 2 | 5 | 3 | 1 |
| Multi-segment prefix support | 2 | 5 | 2 | 1 |
| Diff size and review burden | 4 | 2 | 3 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Every AsyncAPI-derived artefact — `schema dump`/`check`/`init`, `schema acl`, `schema ha-discovery`, `schema openhab`, the `_meta/registry` payload and runtime HA discovery — describes the topics a prefixed app actually uses, honouring the contract already documented in `docs/reference/mqtt-topics.md:100-107`.
- Apps that set no `topic_prefix` produce byte-identical output to today, because the prefix equals `app.name` by definition; this is enforced by regression test rather than asserted in prose.
- Home Assistant `object_id` and `unique_id` remain stable across no prefix, a single-segment prefix and a multi-segment prefix, so no user's existing entities are orphaned by the fix.
- A serialised document is self-describing: any consumer can recover the prefix from `info.x-cosalette-topic-prefix` without importing the application.
- The identity-vs-address ruling closes the `network_level` enforcement bug — a prefixed app with `x-cosalette-enforcement.network_level: true` no longer filters its slice to zero channels and refuses to start in strict mode.
- Existing documents without the new key keep loading unchanged, since the loader performs no unknown-extension rejection (`_schema/_loader_helpers.py:30-115`) and the fallback is precisely today's behaviour.
- The `--topic-prefix` flag gives CI gates a way to validate prefixed deployments without running configure hooks or loading an env file.

### Negative

- `x-cosalette-topic-prefix` enlarges the contract surface: it must be documented, round-trip tested and kept in step with the loader and every consumer generator.
- The change touches generation, the loader, the registry, address parsing, ACL derivation, both consumer generators, three runtime call sites and the CLI — a wide diff for a single PR, with a correspondingly wide regression surface.
- `App.asyncapi()` gains a cache key, so the previously trivial `_asyncapi_cache` invalidation (including the explicit delete at `_schema/_cli_helpers.py:284`) and the `_discovery_payloads_cache` key both become things that can be got wrong.
- Anyone who has already worked around the bug by setting `App(name=...)` to the desired prefix will see their addresses change once they move the value to `MQTT__TOPIC_PREFIX`; the workaround remains valid, but the two configurations are no longer equivalent for `x-cosalette-app`.
- A hand-written document that omits `x-cosalette-topic-prefix` silently gets `app_name` semantics — correct, but not self-evident without reading the reference documentation.

_2026-09-08_
