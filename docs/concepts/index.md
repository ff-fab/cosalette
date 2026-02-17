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

    IoC composition root, decorator registration, "FastAPI for MQTT."

    [:octicons-arrow-right-24: Architecture](architecture.md)

-   **Device Archetypes**

    ---

    Command & Control vs Telemetry — two first-class device types.

    [:octicons-arrow-right-24: Device Archetypes](device-archetypes.md)

-   **Hexagonal Architecture**

    ---

    Ports, adapters, and the dependency rule.

    [:octicons-arrow-right-24: Hexagonal Architecture](hexagonal.md)

-   **MQTT Topics**

    ---

    Home Assistant-aligned topic conventions.

    [:octicons-arrow-right-24: MQTT Topics](mqtt-topics.md)

-   **Configuration**

    ---

    Settings hierarchy: model defaults → .env → environment → CLI.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   **Application Lifecycle**

    ---

    Bootstrap → register → connect → run → shutdown.

    [:octicons-arrow-right-24: Application Lifecycle](lifecycle.md)

-   **Error Handling**

    ---

    Structured errors, fire-and-forget, dual output.

    [:octicons-arrow-right-24: Error Handling](error-handling.md)

-   **Health & Availability**

    ---

    Heartbeats, per-device availability, LWT integration.

    [:octicons-arrow-right-24: Health & Availability](health-reporting.md)

-   **Logging**

    ---

    JSON and text formatters, UTC timestamps, correlation fields.

    [:octicons-arrow-right-24: Logging](logging.md)

-   **Testing**

    ---

    Three-layer strategy: domain, device, integration.

    [:octicons-arrow-right-24: Testing](testing.md)

</div>
