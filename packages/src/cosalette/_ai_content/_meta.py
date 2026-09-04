"""Shared AI content metadata and short content functions.

Provides metadata (topics list, version features), version retrieval, and
shorter content functions like conventions, prime, and what's new.
"""

from __future__ import annotations

import functools
import importlib.metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

# Available topics for help — must stay in sync with get_help_content() branches
AVAILABLE_TOPICS = [
    "telemetry",
    "testing",
    "configuration",
    "architecture",
    "commands",
    "health",
    "scheduling",
    "resilience",
    "sub-entities",
    "triggerable",
    "multi-device",
    "contracts",
    "manifest",
    "react",
    "router",
    "migration",
    "availability",
    "persistence",
    "consumer",
    "consumer-overrides",
    "discovery",
]

# Version feature mapping for upgrade guidance.
#
# Keys MUST match the ACTUAL released version in which each feature shipped
# (cross-check CHANGELOG.md / git tags). Per release-please-config.json
# (bump-patch-for-minor-pre-major: true), in this pre-1.0 project a `feat:` or
# `fix:` commit bumps the PATCH version and only a BREAKING change bumps the
# MINOR version — do NOT assume `feat:` implies a minor (0.x.0) bump when
# choosing a key. Not every release needs an entry; gaps are fine.
VERSION_FEATURES: dict[str, list[str]] = {
    "0.3.0": [
        "name=callable — declarative multi-device registration "
        "(see: cosalette ai help multi-device)",
        "on_configure — dynamic device registration "
        "(see: cosalette ai help configuration)",
        "ctx.commands() — command channel + sub-topic routing "
        "(see: cosalette ai help commands)",
        "HealthCheckable — health monitoring + auto-restart "
        "(see: cosalette ai help health)",
        "sleep_until / schedule= — wall-clock scheduling "
        "(see: cosalette ai help scheduling)",
        "retry/backoff — resilience patterns (see: cosalette ai help resilience)",
        "ctx.sub_entity() — scoped sub-components "
        "(see: cosalette ai help sub-entities)",
    ],
    "0.3.1": [
        "python -m cosalette — universal CLI fallback",
        "MCP server auto-registration in ai init",
    ],
    "0.3.2": [
        "triggerable= — on-demand MQTT-triggered telemetry "
        "(see: cosalette ai help triggerable)",
    ],
    "0.3.3": [
        "enabled=callable — deferred settings-derived enabled flag on decorators "
        "(see: cosalette ai help multi-device)",
        "store=callable — lazy store resolution at bootstrap",
    ],
    "0.3.4": [
        "setting_ref() — inspectable settings bindings "
        "(see: cosalette ai help configuration)",
        "Contract metadata — summary, state_model, payload_model, behavior, effects "
        "on telemetry/command (see: cosalette ai help contracts)",
        "cosalette manifest — print app registry manifest as JSON or table "
        "(see: cosalette ai help manifest)",
    ],
    "0.3.7": [
        "Contract metadata — summary, behavior, effects on @app.device() "
        "and add_device() (see: cosalette ai help contracts)",
    ],
    "0.3.10": [
        "Per-device callable schedule= — when name=callable, schedule= also accepts "
        "a CronSpec callable (per_device_config) -> str | CronSchedule, giving each "
        "device its own cron schedule (see: cosalette ai help scheduling, "
        "cosalette ai help multi-device)",
    ],
    "0.3.12": [
        "MQTT TLS client settings — mqtt.tls, mqtt.tls_ca_file, and mutual-TLS "
        "cert/key fields (see: cosalette ai help configuration)",
        "MCP server is stdio-only; SSE transport is intentionally unsupported "
        "for local dynamic import safety",
    ],
    "0.4.3": [
        "unavailable_on on @app.command — transport availability signaling: "
        "declare which exceptions mark the device offline; framework suppresses, "
        "publishes offline to availability topic, and auto-recovers on next success "
        "(see: cosalette ai help availability)",
        "ctx.mark_unavailable() — dynamic availability control from inside handlers; "
        "same auto-recovery semantics (see: cosalette ai help availability)",
        "AppHarness.assert_state() / assert_subscribed() — deep JSON subset + retain "
        "check, subscription assertion (see: cosalette ai help testing)",
        "AppHarness.inject_command() accepts str | dict payload — dict auto-serialized "
        "(see: cosalette ai help testing)",
    ],
    "0.5.0": [
        "Orphaned retained-topic cleanup — apps with store= configured automatically "
        "clear state/availability retained topics for entities removed from config "
        "since the last run, on the first MQTT connect; prevents ghost entities in "
        "Home Assistant (see: cosalette ai help availability, ADR-048)",
        "timeout= on @app.telemetry — per-handler invocation backstop; a hung adapter "
        "call raises TimeoutError (composes with retry) instead of wedging the poll "
        "loop. BEHAVIOR CHANGE: omitting timeout now auto-defaults to the poll "
        "interval — pass timeout=None to disable "
        "(see: cosalette ai help resilience, ADR-024).",
    ],
    "0.4.0": [
        "@app.react — domain-event reactors for state objects: reactor fires at "
        "execution boundaries when state has pending events "
        "(see: cosalette ai help react)",
        "BREAKING: @app.device handlers must be async generators (add yield after "
        "each unit of work); plain coroutines now raise TypeError",
        "yield in @app.device is the reaction boundary — reactors fire here "
        "before the next ctx.sleep()",
        "Typed handler contracts — Pydantic v2 TypeAdapter validates/serializes "
        "payload and return annotations at runtime; Depends, Payload, Topic, Message "
        "markers; raw str escape hatch (see: cosalette ai help contracts)",
        "Router — composition primitive for multi-module apps: prefix, tags, "
        "topic prefixing, scoped adapters (see: cosalette ai help router)",
        "AsyncAPI manifest inspection — cosalette manifest produces JSON/table "
        "with contract metadata for code generators and doc tooling "
        "(see: cosalette ai help manifest)",
    ],
    "0.5.1": [
        "store= now optional — when omitted, the framework auto-resolves a default "
        "JsonFileStore from the app name (<NAME>_STORE_PATH env, else "
        "$XDG_STATE_HOME/<name>/store.json, else ~/.local/state/<name>/store.json), "
        "so ADR-048 orphaned retained-topic cleanup works with zero config. Pass "
        "store=None to opt out; pass an explicit Store/factory to override "
        "(see: cosalette ai help persistence, ADR-049).",
        "set_default_store_backend() — process-wide override of the auto-resolved "
        "default store backend (e.g. SqliteStore); call once at startup before "
        "constructing any App; pass None to reset to JsonFileStore "
        "(see: cosalette ai help persistence, ADR-049).",
    ],
    "0.5.2": [
        "Ephemeral default-store startup WARNING — when the auto-resolved default "
        "store is ephemeral in a container, no <NAME>_STORE_PATH is set, and the "
        "app's entity set may vary by config (callable name=/enabled= or "
        "@app.on_configure hooks), the framework emits a WARNING at bootstrap. "
        "Static apps are exempt and also skip the ADR-048 snapshot write (no "
        "store.json created). "
        "(see: cosalette ai help persistence, ADR-049).",
    ],
    "0.5.3": [
        "app.store — public read-only accessor for the configured store backend",
        "app.store_is_default — distinguish auto-resolved vs explicit store",
        "app.has_dynamic_entities — public predicate for entity-set classification",
        "TelemetryRegistration, CommandRegistration, DeviceRegistration, "
        "PeriodicRegistration — exported public type aliases for type annotations",
    ],
    "0.5.5": [
        "app.settings_class — public read-only accessor for the App's "
        "Settings subclass (structural wiring tests; replaces app._settings_class)",
        "app.state_factories — public read-only accessor for registered @app.state "
        "factory descriptors (replaces private app._state_factories)",
        "app.stream_registrations / router.stream_registrations — public accessor for "
        "@app.stream / @router.stream handlers (App + Router parity via shared mixin)",
        "StreamRegistration, StateRegistration — new exported public type aliases "
        "for type annotations",
        "schema check/init consistency — @app.stream handlers no longer reported as "
        "spurious EXTRA by cosalette schema check (ADR-033)",
    ],
    "0.5.6": [
        "state_model= / payload_model= on @app.device(), add_device(), and "
        "@router.device() — contract-metadata parity with @app.telemetry and "
        "@app.command. state_model types the device state channel for "
        "cosalette schema init; payload_model is exposed in the manifest for "
        "symmetry (device /set channels are not yet schema-emitted) "
        "(see: cosalette ai help contracts, cosalette ai help manifest).",
        "ha-discovery / openHAB generators now surface consumer annotations from "
        "union payload variants — channels whose payload is oneOf/anyOf/allOf "
        "(e.g. telemetry+command shared channels) now produce HA entities and "
        "openHAB items instead of being silently skipped "
        "(see: cosalette ai help contracts, cosalette ai help manifest).",
        "App(retained_cleanup=...) — tri-state opt-out/opt-in for ADR-048 "
        "retained-topic cleanup and the ephemeral-store startup WARNING: "
        "False skips cleanup + warning while keeping the store for persist=; "
        "True forces cleanup on for apps with import-time config-derived names; "
        "None (default) preserves the existing auto-heuristic unchanged "
        "(see: cosalette ai help persistence, ADR-049).",
    ],
    "0.5.7": [
        "x-cosalette-app channel ownership — cosalette schema dump / init now "
        "emit x-cosalette-app: <app.name> on every channel from the App registry "
        "(ADR-033). Downstream consumers (schema ha-discovery, network slicing, "
        "ACL) resolve the owning app via this tag, and it survives regeneration "
        "instead of being hand-added and stripped on every regen "
        "(see: cosalette ai help manifest).",
        "consumer() schema producer — cosalette.schema.consumer(**meta) + "
        "ConsumerMeta give apps a typed, single-source way to attach "
        "x-cosalette-consumer HA/OpenHAB discovery metadata to model fields "
        "(keys typo-checked by a type checker at author time) instead of "
        "hand-built dicts "
        "(see: cosalette ai help consumer).",
    ],
    "0.5.9": [
        "cosalette ai init --check — verify the instruction file is current; "
        "exits non-zero when stale/missing (CI-gate friendly)",
        "cosalette ai init --force now preserves downstream frontmatter keys "
        "(e.g. paths:) added for other agent tools",
        "--opencode deprecated in favour of --kilo; "
        "kilo.jsonc comments preserved on update",
    ],
    "0.5.10": [
        "ctx.mark_available() — symmetric counterpart to ctx.mark_unavailable(); "
        "publishes retained 'online' and clears the unavailable flag. "
        "@app.command still auto-recovers on next success; @app.telemetry and "
        "@app.device do NOT auto-recover — call mark_available() explicitly "
        "(see: cosalette ai help availability, ADR-047)",
        "cosalette.schema.temperature(display_name) / percent(display_name, "
        "*, icon=None) — semantic presets over consumer() for the standard "
        "°C measurement and percentage measurement field shapes "
        "(see: cosalette ai help consumer).",
    ],
    "0.6.0": [
        "state_model= on @app.stream(), add_stream(), and @router.stream() — "
        "stream handlers can finally declare the contract for the static "
        "retained {prefix}/{stream}/state topic they publish to. Stream "
        "handlers are async generators yielding None, so there is no return "
        "annotation to infer from; state_model is the only contract source "
        "(see: cosalette ai help contracts, ADR-045, ADR-046).",
        "BREAKING: state_model= is now runtime load-bearing on @app.stream AND "
        "@app.device. Every ctx.publish_state() payload from a handler that "
        "declared state_model is validated and normalised against it, raising "
        "ReturnValidationError on a mismatch. Previously @app.device's "
        "state_model only typed the AsyncAPI state channel. One rule now "
        "covers every publishing archetype: if you declare state_model, "
        "published state is validated. Migration — for device handlers that "
        "publish non-conforming payloads, either fix the payload to match the "
        "model or drop state_model= to return to unvalidated publishing. "
        "Handlers that never declared state_model are unaffected; validation "
        "is skipped entirely and costs nothing. Note that a declared model "
        "also *normalises*: field aliases, custom serialisers, and coercion "
        "apply, so an int 3 for a float field publishes as 3.0. Scope is the "
        "static state topic only — ctx.publish() and sub-entity channels are "
        "not validated (see: cosalette ai help contracts, ADR-045 amendment).",
        "Stream and periodic contract metadata now reaches an artifact — "
        "build_registry_snapshot() / format_registry_table() and the "
        "cosalette_inspect_app MCP tool gained streams and "
        "periodic sections. summary/state_model/behavior/effects on "
        "@app.stream and summary/behavior on @app.periodic were previously "
        "accepted, stored on the registration, and read by nothing. Periodic "
        "tasks remain absent from the generated AsyncAPI (no MQTT presence, "
        "ADR-041); streams now emit an AsyncAPI state channel — see the "
        "stream-AsyncAPI entry in this list "
        "(see: cosalette ai help manifest, cosalette ai help contracts).",
        "BREAKING: dependencies= removed from cosalette.Router(), "
        "app.include_router(), and every router decorator (telemetry, command, "
        "device, stream, periodic). It was a reserved placeholder that only ever "
        "raised NotImplementedError; passing it now raises TypeError. Delete the "
        "argument and declare dependencies per handler parameter with "
        "cosalette.Depends() (see: cosalette ai help router, ADR-044 amendment)",
        "Optional() — explicit binding marker for optional dependency injection: "
        "Annotated[T | None, Optional()] resolves the provider if one is "
        "registered, otherwise falls back to the parameter default (implicitly "
        "None). Bare T | None on an injected parameter is still rejected — "
        "optionality is never inferred from type syntax; Optional() is the "
        "explicit opt-in (see: cosalette ai help contracts, ADR-053)",
        "BREAKING: ambiguous subclass match now raises TypeError when a "
        "parameter annotation matches multiple registered providers by "
        "subclass, instead of silently returning the first dict-order match. "
        "Affects plain and Optional()-annotated parameters resolved via adapter "
        "subclass matching. Migration — if you have a port hierarchy with "
        "multiple subtype adapters registered, use a more specific annotation "
        "to disambiguate (see: cosalette ai help contracts, "
        "docs/concepts/dependency-injection.md#subclass-matching-and-ambiguity)",
        "BREAKING: @app.stream now emits a fourth x-cosalette-archetype "
        "('stream') as a send/publish {prefix}/{name}/state channel in the "
        "generated AsyncAPI (cosalette manifest / schema dump); excluded from "
        "Home Assistant discovery by default so no entities are silently "
        "created; @app.periodic stays excluded (no MQTT presence). AsyncAPI "
        "documents now contain x-cosalette-archetype: stream, which older "
        "cosalette schema loaders reject — regenerate schema artifacts and "
        "upgrade consumers (see: cosalette ai help manifest, ADR-054).",
        "Concurrent per-entity command dispatch (default) — MQTT read loop "
        "never awaits user command code. Each entity gets a dedicated FIFO "
        "worker task + asyncio.Queue. Per-entity ordering preserved; entities "
        "run concurrently. One entity's slow/hung handler does NOT block "
        "commands for other entities. Fixes silent failure bug where app kept "
        "reporting healthy while commands stopped processing "
        "(see: cosalette ai help commands, ADR-055).",
        "timeout= on @app.command, @app.device, app.add_command(), "
        "app.add_device(), @router.command, and @router.device — "
        "per-invocation backstop via asyncio.wait_for, reusing the telemetry "
        "timeout mechanism. TimeoutError flows through publish_error_safely "
        "to error topic. Composes with unavailable_on=(TimeoutError,) to mark "
        "device offline on timeout. Default None (no timeout) "
        "(see: cosalette ai help commands, cosalette ai help availability, "
        "ADR-055).",
        "maxsize= and backpressure= on @app.command, @app.device, and public "
        "@router.command / @router.device — bounded command queues with "
        "declarable backpressure (drop_newest, drop_oldest, raise), reusing "
        "the stream BackpressurePolicy vocabulary. Applies to router per-entity "
        "worker queue AND ctx.commands() queue. Default maxsize=0 (unbounded, "
        "fully backward compatible). Shared apply_backpressure() helper across "
        "streams, router, device context (see: cosalette ai help commands, "
        "ADR-055).",
        "payload_model= on @app.device now emits a receive channel in AsyncAPI "
        "— devices that declare payload_model subscribe to {prefix}/{device}/set "
        "at runtime and now emit a receive channel (archetype: device) on /set "
        "alongside the existing /state send channel. Devices without "
        "payload_model unchanged. payload_model remains introspection metadata "
        "— does NOT runtime-validate inbound payloads; only state_model is "
        "runtime load-bearing for ctx.publish_state "
        "(see: cosalette ai help commands, cosalette ai help contracts, "
        "ADR-055).",
    ],
    "0.6.1": [
        "Consumer code generation correctness — cosalette schema ha-discovery "
        "and schema openhab now emit valid output for bidirectional and typed "
        "apps instead of confidently-wrong config. Command entities carry a "
        "_cmd suffix so a state entity and a command entity for the same "
        "device+property no longer overwrite each other on the broker; commands "
        'publish a JSON envelope command_template ({"prop": {{ value }}}) '
        "instead of a bare scalar the app would reject; optional fields "
        "(int | None) infer their real type instead of degrading to "
        "string/sensor; number entities emit min/max/step from "
        "minimum/maximum/multipleOf; select emits options from enum; read_only "
        "is honoured (state-only, read-only component); and platform-rejected "
        "keys (unit/state_class on a binary_sensor) are dropped. OpenHAB now "
        "emits one Thing per device (no duplicate UIDs), direction-aware Item "
        "names that link to the channel they name, formatBeforePublish JSON "
        "envelopes on command channels, and on/off on boolean switches. Device "
        "extraction now handles nested/router addresses. State/telemetry "
        "output is unchanged, so read-only apps need no action "
        "(see: cosalette ai help consumer).",
    ],
    "0.6.2": [
        "cosalette.schema.ha_discovery() / openhab() — typed producers for "
        "x-cosalette-ha-discovery and x-cosalette-openhab, mirroring "
        "consumer()'s TypedDict-parity pattern. Both gain an open passthrough "
        "field (ha_discovery(extra={...}), openhab(channel_params={...})) that "
        "reaches HA/openHAB platform keys the curated fields don't cover, plus "
        "openhab(channel_type=...) to override the inferred .things channel "
        "type (fixes a Color item bound to a string channel). "
        "cosalette.schema.merge() combines consumer()/ha_discovery()/openhab() "
        "into the single dict Field(json_schema_extra=...) accepts "
        "(see: cosalette ai help consumer-overrides, ADR-056).",
        "cosalette.schema.ha_entities() / ha_entity() — composite Home "
        "Assistant entities spanning a whole payload model instead of one "
        "entity per property. Attached at the model level "
        "(pydantic.ConfigDict(json_schema_extra=...)), not a field. "
        "component-aware payload builders give component='light' a real "
        "schema:json default, drop the invalid generic state/command topics "
        "for component='climate', and merge a device archetype's paired "
        "/state + /set channels into one entity instead of two incomplete "
        "ones (see: cosalette ai help consumer-overrides, ADR-057).",
        "ha-discovery now emits availability, per-device device blocks, and "
        "origin on every entity (scalar and composite). A resolved device "
        "gets availability (its own {app}/{device}/availability topic plus "
        "the app-level {app}/status heartbeat/LWT topic, availability_mode: "
        '"all") instead of never going unavailable; each resolved device '
        "gets its own HA device (identifiers: cosalette_<app>_<device>) "
        "linked via via_device to an app-level bridge device, instead of "
        "every entity in an app sharing one HA device; a diagnostic "
        "connectivity binary_sensor is emitted once per app with a named "
        "device so the bridge device (and via_device) actually appears in "
        "HA; origin (name, sw_version) is added for free "
        "(see: cosalette ai help consumer, ADR-058).",
        "app.discovery(discovery_prefix=, enrich=) — opt-in runtime Home "
        "Assistant MQTT discovery: publishes retained discovery config "
        "payloads on first MQTT connect, generated from the app's live, "
        "already-expanded registry, so settings-derived (ADR-023 callable "
        "name=) entity names are always correct. enrich=(channel, prop, "
        "config) -> None runs as the final step before each entity payload "
        "is built — the escape hatch for whatever consumer()/ha_entities() "
        "can't express. Orphaned discovery topics for devices removed from "
        "config are cleared the same way ADR-048 already clears state/"
        "availability. ha-discovery/openhab now exit non-zero and warn on "
        "stderr instead of silently printing [] when a schema has channels "
        "eligible for discovery but nothing annotated "
        "(see: cosalette ai help discovery, ADR-059).",
    ],
    "0.8.0": [
        "triggerable= is now a trigger-source declaration, not just a flag: "
        '"mqtt" (== True), "local", "both", or None/False. "local" wakes a '
        "telemetry entity from in-process code with no MQTT subscription; "
        '"both" accepts either source. BREAKING: TelemetryRegistration.'
        "triggerable is now TriggerSource | None instead of bool "
        "(see: cosalette ai help triggerable, ADR-064).",
        "EntityNotifier — new injectable, callable as notify(entity_name), "
        "that arms a triggerable='local'/'both' telemetry entity from an "
        "@app.state factory, adapter, lifecycle hook or handler. Thread-safe "
        "(hops to the event loop by itself) and fails loudly: "
        "UnknownEntityError / NotifierNotReadyError rather than a silent "
        "no-op (see: cosalette ai help triggerable, ADR-064).",
        'TriggerPayload.source — "scheduled" | "mqtt" | "local", so a '
        "handler can tell what armed the run, not just that it was triggered "
        "(see: cosalette ai help triggerable, ADR-064).",
        "Root (unnamed) devices may now be triggerable='local' — the "
        "root-device guard is narrowed to MQTT trigger sources only, since "
        "a local trigger needs no topic segment (ADR-064).",
        "@app.device(triggerable='local') + DeviceTrigger — devices can now "
        "join the same local trigger mechanism. The device owns its loop, so "
        "it injects a DeviceTrigger and awaits trigger.wait(timeout=...) "
        "itself; the same EntityNotifier wakes telemetry entities and "
        "devices by name. Devices accept 'local' only, because "
        "{prefix}/{name}/set is already the device command topic. Additive: "
        "existing devices are unaffected "
        "(see: cosalette ai help triggerable, ADR-065).",
        "min_interval= — opt-in storm throttle for trigger-initiated runs "
        "on @app.telemetry and @app.device. Leading edge: the first wake "
        "after a quiet period runs immediately. Trailing edge: wakes "
        "arriving inside the window coalesce into exactly one run when it "
        "reopens, carrying the last payload — nothing is dropped. interval= "
        "heartbeats are never throttled and never consume a pending wake. "
        "Requires triggerable=; default None keeps today's behaviour exactly "
        "(see: cosalette ai help triggerable, ADR-066).",
        "triggerable= now combines with group= — a coalescing-group member "
        "may declare a trigger source. The wake is per member: arming one "
        "member runs that member alone, members armed at the same moment "
        "share one batch and one adapter session, and a member with no new "
        "input is never invoked. Unlike an ungrouped entity, a triggered run "
        "does not rephase the member's interval= heartbeat, which stays "
        "anchored to the group's shared epoch. min_interval= applies per "
        "member (see: cosalette ai help triggerable, ADR-067).",
    ],
    "0.9.0": [
        "state_model= is now a real return-value contract on @app.telemetry "
        "and @app.command, so the one rule — declare state_model and published "
        "state is validated — finally holds on all four publishing archetypes. "
        "BREAKING: a handler whose payload never matched its declared model "
        "now raises ReturnValidationError on first boot (usually a missing "
        "required field); the error goes to {prefix}/{name}/error and the "
        "state publish is suppressed. Fix the payload, or drop state_model= to "
        "go back to unvalidated publishing "
        "(see: cosalette ai help contracts, ADR-068).",
        "state_model= outranks the return annotation. BREAKING: a handler "
        "declaring both state_model=M and a differently typed annotation — the "
        "common -> dict[str, object] — used to be governed by the annotation "
        "and is now governed by state_model= (ADR-068 clause A).",
        "A non-conforming plain dict can no longer ride the dump_python fast "
        "path. BREAKING: normalize_return dumps with warnings='error', so a "
        "Pydantic serializer mismatch that used to be a swallowed warning now "
        "routes through validate_python and raises (ADR-068 clause B).",
        "Validated state dumps with exclude_none=True on every archetype, so "
        "an absent optional field is an omitted key rather than an explicit "
        "null. BREAKING: this changes the @app.device / @app.stream wire "
        "payload for any state_model with optional fields — Home Assistant "
        "value_templates and exact-payload contract tests may need updating "
        "(ADR-068 clauses C and D).",
        "Registration emits a UserWarning when state_model= and the return "
        "annotation name different types, stating that state_model= wins. "
        "-> M, -> M | None and -> None stay silent. Under pytest's "
        'filterwarnings = ["error"] that warning is an ERROR: remove the loose '
        "return annotation and leave state_model= as the sole contract "
        "(ADR-068 clause F).",
    ],
}


def get_version() -> str:
    """Get the cosalette package version."""
    try:
        return importlib.metadata.version("cosalette")
    except Exception:
        return "unknown"


@functools.lru_cache(maxsize=1)
def _get_package_assets_dir() -> Path:
    """Get the path to packaged guidance assets."""
    try:
        import cosalette

        package_path = Path(cosalette.__file__).parent
        return package_path / "assets" / "guidance"
    except ImportError, AttributeError:
        # Fallback if running in development
        return Path(__file__).parent / "assets" / "guidance"


@functools.lru_cache(maxsize=1)
def get_conventions_content() -> str:
    """Get the cosalette framework conventions and patterns instruction content."""
    try:
        assets_dir = _get_package_assets_dir()
        instructions_file = assets_dir / "cosalette.instructions.md"
        if instructions_file.exists():
            return instructions_file.read_text()
        else:
            return (
                "cosalette framework instructions not found. "
                "Run 'cosalette ai init' to install the instruction file."
            )
    except Exception as e:
        return f"Error reading cosalette instructions: {e}"


@functools.lru_cache(maxsize=1)
def get_prime_content() -> str:
    """Get the cosalette framework bootstrap overview for starting development."""
    version_str = get_version()

    return f"""🚀 cosalette v{version_str} — AI Agent Bootstrap

📋 Essential Commands:
   cosalette ai init           Install instruction file + manage AGENTS.md (CLAUDE.md)
   cosalette ai help <topic>   Topic-specific guidance
   cosalette ai init --force   Refresh instruction file, latest templates
   cosalette ai init --check   Verify instructions are current (exit 1 if stale)

⚡ CLI Invocation:
   cosalette                   If installed as script entry point
   python -m cosalette         Universal fallback (always works)
   uv run python -m cosalette  In uv-managed projects

🔌 MCP Server:
   cosalette ai mcp serve      Start MCP server for VS Code

   Register in .vscode/mcp.json:
   {{
     "servers": {{
       "cosalette": {{
         "command": "python",
         "args": ["-m", "cosalette", "ai", "mcp", "serve"]
       }}
     }}
   }}

   Note: 'cosalette ai init' auto-registers if cosalette[mcp] installed

🎯 Framework Patterns:
   • Declarative app composition via App() + decorators
   • @app.telemetry(), @app.command(), @app.device() registration
   • @app.device handlers are async generators — yield marks the reaction boundary
   • @app.react() for domain-event reactors: state stays pure, I/O lives in reactors
   • name=callable for multi-device registration from settings
   • Type-based dependency injection + init= factories
   • Persistent state via DeviceContext.state + callable store factories

📁 Project Structure:
   .github/instructions/       AI agent instruction files (install via 'ai init')
   AGENTS.md                  Auto-managed framework pointer (canonical installs only)
   CLAUDE.md                  Auto-managed framework pointer (if file exists)
   app.py or main.py          App composition root (recommended)
   .env                       Environment configuration

🔗 Key Capabilities:
   • Publishing strategies: OnChange, Every, scheduled intervals
   • Persistence policies: SaveOnChange, SaveOnShutdown
   • Health monitoring + error publishing
   • Settings inheritance from cosalette.Settings
   • Async lifecycle management

📚 Deep Dive Topics:
   cosalette ai help architecture   — Design principles + rationale
   cosalette ai help telemetry      — Device registration patterns
   cosalette ai help testing        — Framework testing strategies
   cosalette ai help configuration  — Settings + environment
   cosalette ai help commands       — Command handling + routing
   cosalette ai help health         — Health monitoring + auto-restart
   cosalette ai help scheduling     — Cron scheduling + wall-clock alignment
   cosalette ai help resilience     — Retry strategies + circuit breakers
   cosalette ai help sub-entities   — Sub-component lifecycle management
   cosalette ai help triggerable    — Trigger sources: MQTT + in-process
                                      (telemetry and devices)
   cosalette ai help multi-device   — Declarative multi-device registration
   cosalette ai help contracts      — Contract metadata on registrations
   cosalette ai help manifest       — Inspect app registration surface
   cosalette ai help react          — Domain-event reactors + async-generator
                                      device semantics
   cosalette ai help availability   — Transport availability signaling
   cosalette ai help persistence     — Store backends, default resolution,
                                      persist= policies
   cosalette ai help discovery      — Runtime Home Assistant MQTT discovery"""


def get_whats_new_content(from_version: str) -> str:
    """Generate What's New section for versions after from_version.

    Args:
        from_version: Starting version to show features from (exclusive)

    Returns:
        Formatted what's new content, or empty string if invalid/no new features
    """
    try:
        base_version = Version(from_version)
    except Exception:
        return ""  # Invalid version format

    # Find all versions newer than from_version
    newer_versions = []
    for version_str in VERSION_FEATURES:
        try:
            version = Version(version_str)
            if version > base_version:
                newer_versions.append((version, version_str))
        except InvalidVersion:
            continue  # Skip invalid versions

    if not newer_versions:
        return ""  # No newer versions found

    # Sort by version (newest first for display)
    newer_versions.sort(reverse=True)

    content_lines = [f"## What's New (since {from_version})", ""]

    for _, version_str in newer_versions:
        features = VERSION_FEATURES[version_str]
        content_lines.append(f"### {version_str}")
        for feature in features:
            content_lines.append(f"- {feature}")
        content_lines.append("")

    return "\n".join(content_lines).rstrip()
