---
status: Accepted
date: 2026-08-11
impact: high
tags: [mqtt, discovery, architecture, cli, persistence]
---

# ADR-059: Runtime Home Assistant Discovery Publication with Enrichment Hook

## Status

Accepted **Date:** 2026-08-11

## Context

cos-0p16 (Proposal Finding 23, tracked under epic cos-v0sg — consumer generator overhaul) is the last of cap-10u.6's three closing conditions for cosalette-apps' wiz2mqtt build (Findings 20/21 shipped as ADR-057/ADR-056). `cosalette schema ha-discovery`/`cosalette schema openhab` (`_schema/_cli.py`) end in `typer.echo` — grepping the package for `HaDiscoveryGenerator`/`OpenHabGenerator` outside `_schema/` returns nothing, so nothing in the framework ever publishes a discovery payload against the live application; generation only ever runs against a checked-in or hand-dumped YAML file. Because generation and runtime diverge, this has already produced two shipped defects. Evidence 1: velux2mqtt registers covers with a callable `name=` keyed on user settings (ADR-023). The static pipeline imports the app with no settings and no bootstrap, so callable names are still unexpanded at generation time; `ha-discovery` emitted a payload with `state_topic: velux2mqtt/cover_device/state` — a topic derived from the handler's Python qualname that no runtime publish ever uses — while the real per-cover topics got no discovery at all. ADR-051 already named this the 'phantom-entity class' and put a design gate on a deferred 'settings-resolving dump mode' for the static side, but its own Option 3 analysis calls the interim workaround (retained `_meta/registry` + `mosquitto_sub -C1 | schema ha-discovery`) 'operationally fragile ... requires a running broker and a live, fully-bootstrapped application instance'. Evidence 2: five of the eight apps in this monorepo now hand-roll an identical integration test that runs the real app, captures MQTT publishes, and asserts every discovery `state_topic` was actually published at runtime — the same check reinvented six times because the framework itself never guarantees it. Evidence 3: `jeelink2mqtt` and `suncast` carry zero `x-cosalette-consumer` annotations; `cosalette schema ha-discovery` prints `[]` and exits 0 for both. An app can be fully wired, pass `schema:check`, and ship no Home Assistant discovery at all with no signal anywhere in CI. Separately, ADR-048 already persists a resolved entity snapshot per app and clears orphaned retained `state`/`availability` topics for entities removed from config between runs, but that mechanism is keyed by entity name + retained-kind and never touches `homeassistant/.../config` topics, so a device removed from config still leaves a permanent ghost entity in Home Assistant's UI.

## Decision

Add an opt-in `App.discovery(discovery_prefix='homeassistant', enrich=None)` API (`_app/_discovery.py`). When called, the framework builds `HaDiscoveryGenerator` payloads from the app's own live, already-expanded registry — reusing `App.asyncapi()` (built after `expand_name_specs` has run) round-tripped through the existing, fully-tested `load_schema()`/`InlineSchemaSource` pipeline rather than a new dict-to-registry path — and publishes them as retained MQTT messages on the first successful connect (`_wiring/_discovery.py::publish_discovery`, wired into `register_connect_reannounce`/`publish_startup_snapshot` alongside the existing ADR-048 calls). Because the source registry is built after callable `name=` specs have already been resolved against real settings, a phantom topic can no longer be constructed — Evidence 1 is dissolved structurally, not papered over. `HaDiscoveryGenerator` gains an `enrich: (channel, prop, config) -> None` hook, called once per emitted entity as the final step before its payload is built (`prop` is `None` for a channel-level ADR-057 composite entity), giving an app an escape hatch for whatever the curated schema surface can't yet express — mirroring the 'curated front door plus an open back door' principle ADR-056 already established for `extra`/`channel_params`. ADR-048's orphaned-topic cleanup is extended with a parallel, discovery-specific snapshot: `reconcile_discovery_topics` diffs the *topic string set* (not entity name + kind, since a discovery topic is keyed by `(component, node_id, object_id)` under a separate `homeassistant/...` namespace) against the previous run's snapshot in the same configured `Store`, publishing empty retained messages for orphans, guarded by the same fail-closed, first-connect-only, name/topic-shape validation convention ADR-048 established. Finally, `ha-discovery`/`openhab` now exit non-zero and warn on stderr when a registry has at least one consumer-visible channel but produced zero payloads (Evidence 3's silent-`[]` case), and separately warn when a `x-cosalette-consumer` block was found deeper in a payload schema than the loader's one-level array/object descent can reach (`SchemaRegistry.unreachable_consumer_channels`, populated in `load_schema()`).

```python
app = cosalette.App(name="velux2mqtt", version="0.3.0")
app.discovery()  # opt-in; publishes on first MQTT connect

@app.device(name=lambda settings: settings.cover_names)
async def cover(ctx: cosalette.DeviceContext):
    ...

# Escape hatch for whatever consumer()/ha_entities() can't express yet:
def _enrich(channel, prop, config):
    if config.get("device_class") == "cover":
        config["device_class"] = "shutter"

app.discovery(enrich=_enrich)
```

## Decision Drivers

- Dissolve the ADR-051 phantom-entity class structurally rather than approximating it from the static/offline side with a settings-resolving dump mode.
- Reuse the existing, exhaustively-tested schema loader and HaDiscoveryGenerator rather than a second live-registry code path, so static and runtime discovery cannot silently diverge.
- Keep the feature strictly opt-in — apps that don't target Home Assistant, or already publish discovery out of band, must see zero behaviour change on upgrade.
- Extend, not replace, the ADR-048 orphaned-retained-topic cleanup convention operators and reviewers already understand (opt-in via Store, first-connect-only, fail-closed).
- Close the silent-failure gap (Evidence 3) that let a fully-wired, schema-check-passing app ship zero discovery with no signal in CI or at runtime.
- Preserve the ADR-056 'curated front door, open back door' principle so the enrichment hook survives Home Assistant's vocabulary moving faster than framework releases.
- Minimise new runtime MQTT traffic: discovery is process-lifetime-static, so it publishes once on first connect rather than on every reconnect.

## Considered Options

### Option 1: In-process dump-then-load round trip (chosen)

Serialise App.asyncapi()'s dict to YAML with yaml.safe_dump and feed it back through the existing InlineSchemaSource + load_schema() pipeline to build a live SchemaRegistry, then run it through the unmodified HaDiscoveryGenerator.

- *Advantages:* Reuses 100% of the existing, tested extension-validation, $ref-resolution, and generator code paths — no new parsing logic to maintain or let drift from the file-based CLI behaviour.; Behaviourally identical, in-process, to the already-documented and already-supported two-step workflow (`schema dump --app ... | schema ha-discovery`), so nothing new needs separate verification against the AsyncAPI contract.; The one-time YAML round trip is cached on the App instance after first computation, matching the existing `app.asyncapi()`/`_asyncapi_broker_cache` pattern — negligible steady-state cost.
- *Disadvantages:* Costs one YAML serialise + parse pass at startup for apps that opt in.; Requires the optional `cosalette[schema]` extra (PyYAML) to be installed even though building the AsyncAPI dict itself does not need it — mitigated by `_ensure_schema_deps()` raising the same friendly, install-hint error the CLI already gives.

### Option 2: New load_schema_from_dict() bypassing YAML entirely

Add a sibling to load_schema() that accepts the AsyncAPI dict directly, skipping yaml.safe_load, and reuses only the downstream extraction/ref-resolution helpers.

- *Advantages:* No PyYAML dependency for the runtime path.; Marginally faster — skips one serialise/parse cycle.
- *Disadvantages:* A second, separately-tested code path parallel to load_schema() that can drift from the file-based CLI behaviour over time as either evolves independently.; The schema module's existing test suite exercises the YAML path exhaustively; a dict path would need its own comparable coverage for the same guarantees, doubling the maintenance surface for no behavioural gain.

### Option 3: Static-only fix — implement ADR-051's deferred settings-resolving dump mode

Instead of a runtime publisher, finish ADR-051's design-gated 'settings-resolving dump mode': run configure/expand before building the static AsyncAPI document so schema dump/ha-discovery see expanded names, and leave publication entirely to the operator's existing offline workflow.

- *Advantages:* No new runtime MQTT behaviour to reason about or test.; Keeps discovery entirely offline/CLI-driven, consistent with today's operational model and smaller in scope.
- *Disadvantages:* Does not close Evidence 2 — the runtime cross-check test five apps already hand-roll independently would still be necessary, since generation and the live broker state can still diverge between runs.; Leaves ADR-051's own 'operationally fragile' two-step workflow (mosquitto_sub then schema ha-discovery) as the only way to get discovery derived from live state, for apps that don't want to hand-maintain a checked-in schema file.; Does nothing for orphaned discovery config topics — a device removed from config still leaves a permanent ghost entity in Home Assistant.

### Option 4: Always-on discovery whenever a Store is configured (no opt-in)

Skip the App.discovery() call entirely; publish HA discovery automatically for any app with a configured Store, inferring HA as the default consumer.

- *Advantages:* Zero-config for the common case — nothing to remember to call.; No branch in _run_async for 'was discovery requested'.
- *Disadvantages:* Silently changes the retained-topic surface of every existing app on upgrade to a version carrying this feature, including openHAB-only apps and apps with a private/custom consumer that have no reason to want Home Assistant discovery topics on their broker.; Breaks the minimalism precedent the sibling correctness-fix issue (cos-fr9s) deliberately set — 'no new API surface without an explicit decision to add it' — by making an architectural commitment (HA is the default discovery target) without an opt-in signal from the app author.; Removes the natural place to pass discovery_prefix/enrich configuration, which would then need a different (also new) settings-based mechanism anyway.

## Decision Matrix

| Criterion | In-process dump-then-load round trip | New load_schema_from_dict() bypassing YAML entirely | Static-only fix — implement ADR-051's deferred settings-resolving dump mode | Always-on discovery whenever a Store is configured (no opt-in) |
| --- | --- | --- | --- | --- |
| Closes the ADR-051 phantom-entity class | 5 | 5 | 3 | 5 |
| Implementation risk / code reuse | 5 | 2 | 4 | 4 |
| Operator ergonomics (removes the mosquitto_sub two-step) | 5 | 5 | 2 | 5 |
| Backward compatibility on upgrade | 5 | 5 | 5 | 1 |
| Extensibility (enrichment hook, future consumer types) | 4 | 4 | 2 | 3 |
| Long-term maintenance burden | 5 | 2 | 4 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- The velux2mqtt phantom-entity failure class cannot recur for HA discovery: by construction, a payload is only ever built from names already resolved against real settings.
- The runtime state_topic cross-check test five apps independently hand-rolled is no longer strictly necessary for apps that adopt App.discovery() — the generator now runs against the exact registry that publishes state, in the same process, on every startup.
- A registry with consumer-visible channels but zero annotations (the jeelink2mqtt/suncast shape) now fails loudly in `ha-discovery`/`openhab` instead of silently exiting 0 with `[]`.
- Orphaned discovery entities for devices removed from config no longer linger permanently in Home Assistant's UI, closing the same class of operator confusion ADR-048 already closed for state/availability.
- The enrichment hook gives every app an immediate, typed escape hatch for Home Assistant vocabulary the curated `consumer()`/`ha_discovery()`/`ha_entities()` surface doesn't yet cover, without waiting on a framework release.
- Zero behaviour change for any app that does not call App.discovery() — the new code paths are entirely inert until opted into.

### Negative

- Apps that opt in now depend on the optional `cosalette[schema]` extra (PyYAML) at runtime, not just for CLI tooling; a missing extra now surfaces as a runtime ImportError (with an install hint) rather than only affecting `cosalette schema ...` commands.
- Discovery config topics are only republished on the first successful connect per process lifetime, not on every reconnect — an operator who mutates the broker's retained discovery topics out of band between reconnects (outside this framework's control) will not see them self-heal until the next full app restart.
- The new discovery-topic snapshot is a second persisted key per app in the same Store used by ADR-048, growing the store's schema surface and the number of reserved key prefixes an operator inspecting the store needs to know about.
- openHAB has no equivalent runtime discovery protocol (its .things/.items output is static configuration, not retained MQTT topics), so this ADR's runtime-publication half is HA-only; openHAB remains served exclusively by the offline CLI path, which is a capability asymmetry between the two consumers that this ADR does not resolve.

_2026-08-11_
