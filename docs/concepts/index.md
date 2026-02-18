---
icon: material/lightbulb-outline
---

# Concepts

Understand the ideas and architecture behind cosalette.

These pages explain *why* things work the way they do — the mental models,
design patterns, and architectural decisions that shape the framework.

<div class="grid cards" markdown>

-   **Architecture**

    ---

    Composition root, Inversion of Control, decorator registration, context injection, and the four-phase orchestration model.

    [:octicons-arrow-right-24: Architecture](architecture.md)

-   **Device Archetypes**

    ---

    Two first-class device types — Command & Control (bidirectional coroutines) and Telemetry (periodic polling) — with error isolation.

    [:octicons-arrow-right-24: Device Archetypes](device-archetypes.md)

-   **Hexagonal Architecture**

    ---

    PEP 544 Protocol ports, three adapter registration forms, lazy imports, dry-run mode, and the dependency rule.

    [:octicons-arrow-right-24: Hexagonal Architecture](hexagonal.md)

-   **MQTT Topics**

    ---

    Home Assistant-aligned flat hierarchy with six topic types: state, set, availability, error, global error, and app status.

    [:octicons-arrow-right-24: MQTT Topics](mqtt-topics.md)

-   **Configuration**

    ---

    Type-safe pydantic-settings with layered precedence: CLI flags > env vars > .env > defaults. SecretStr for credentials.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   **Application Lifecycle**

    ---

    Four-phase orchestration: Bootstrap → Registration → Run → Teardown, with signal handling and graceful shutdown.

    [:octicons-arrow-right-24: Application Lifecycle](lifecycle.md)

-   **Error Handling**

    ---

    Structured JSON error payloads, fire-and-forget MQTT publication, per-device isolation, and pluggable error type mapping.

    [:octicons-arrow-right-24: Error Handling](error-handling.md)

-   **Health & Availability**

    ---

    App-level LWT crash detection, structured JSON heartbeats, per-device online/offline availability for Home Assistant.

    [:octicons-arrow-right-24: Health & Availability](health-reporting.md)

-   **Logging**

    ---

    NDJSON structured logs for production, text for development. UTC timestamps, correlation metadata, zero external dependencies.

    [:octicons-arrow-right-24: Logging](logging.md)

-   **Testing**

    ---

    Three-layer strategy (domain, device, integration) with MockMqttClient, FakeClock, AppHarness, and a pytest plugin.

    [:octicons-arrow-right-24: Testing](testing.md)

</div>
