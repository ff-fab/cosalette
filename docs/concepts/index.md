---
icon: material/lightbulb-outline
---

# Concepts

Understand the ideas and architecture behind cosalette.

These pages explain *why* things work the way they do — the mental models,
design patterns, and architectural decisions that shape the framework.

<div class="grid cards" markdown>

-   :material-city-variant-outline:{ .lg .middle .card-icon-right } **Architecture**

    ---

    Composition root, Inversion of Control, decorator registration, context injection, and the four-phase orchestration model.

    [:octicons-arrow-right-24: Architecture](architecture.md)

-   :material-devices:{ .lg .middle .card-icon-right } **Device Archetypes**

    ---

    Three first-class device types — Command (`@app.command()`), Command & Control (`@app.device()`), and Telemetry (`@app.telemetry()`) — with error isolation.

    [:octicons-arrow-right-24: Device Archetypes](device-archetypes.md)

-   :material-hexagon-outline:{ .lg .middle .card-icon-right } **Hexagonal Architecture**

    ---

    PEP 544 Protocol ports, three adapter registration forms, lazy imports, dry-run mode, and the dependency rule.

    [:octicons-arrow-right-24: Hexagonal Architecture](hexagonal.md)

-   :material-message-outline:{ .lg .middle .card-icon-right } **MQTT Topics**

    ---

    Home Assistant-aligned flat hierarchy with six topic types: state, set, availability, error, global error, and app status.

    [:octicons-arrow-right-24: MQTT Topics](mqtt-topics.md)

-   :material-cog-outline:{ .lg .middle .card-icon-right } **Configuration**

    ---

    Type-safe pydantic-settings with layered precedence: CLI flags > env vars > .env > defaults. SecretStr for credentials.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   :material-refresh:{ .lg .middle .card-icon-right } **Application Lifecycle**

    ---

    Four-phase orchestration: Bootstrap → Wire → Run → Teardown, with signal handling and graceful shutdown.

    [:octicons-arrow-right-24: Application Lifecycle](lifecycle.md)

-   :material-alert-circle-outline:{ .lg .middle .card-icon-right } **Error Handling**

    ---

    Structured JSON error payloads, fire-and-forget MQTT publication, per-device isolation, and pluggable error type mapping.

    [:octicons-arrow-right-24: Error Handling](error-handling.md)

-   :material-heart-pulse:{ .lg .middle .card-icon-right } **Health & Availability**

    ---

    App-level LWT crash detection, structured JSON heartbeats, per-device online/offline availability for Home Assistant.

    [:octicons-arrow-right-24: Health & Availability](health-reporting.md)

-   :material-text-box-outline:{ .lg .middle .card-icon-right } **Logging**

    ---

    NDJSON structured logs for production, text for development. UTC timestamps, correlation metadata, zero external dependencies.

    [:octicons-arrow-right-24: Logging](logging.md)

-   :material-filter-check:{ .lg .middle .card-icon-right } **Publish Strategies**

    ---

    Decouple probing from publishing — `Every`, `OnChange`, threshold modes, composition with `|` and `&`, and edge cases.

    [:octicons-arrow-right-24: Publish Strategies](publish-strategies.md)

-   :material-chart-bell-curve-cumulative:{ .lg .middle .card-icon-right } **Signal Filters**

    ---

    Handler-level data transformations — PT1 low-pass, Median, and 1€ adaptive filters for smoothing sensor noise.

    [:octicons-arrow-right-24: Signal Filters](signal-filters.md)

-   :material-database:{ .lg .middle .card-icon-right } **Persistence**

    ---

    Save device state across restarts with pluggable backends
    and composable save policies.

    [:octicons-arrow-right-24: Persistence](persistence.md)

-   :material-test-tube:{ .lg .middle .card-icon-right } **Testing**

    ---

    Three-layer strategy (domain, device, integration) with MockMqttClient, FakeClock, AppHarness, and a pytest plugin.

    [:octicons-arrow-right-24: Testing](testing.md)

-   :material-magnify-scan:{ .lg .middle .card-icon-right } **Registry Introspection**

    ---

    Machine-readable snapshots of all registered devices, telemetry, commands, and adapters for diagnostics and tooling.

    [:octicons-arrow-right-24: Registry Introspection](introspection.md)

</div>
