---
icon: material/hammer-wrench
---

# How-To Guides

Step-by-step instructions for common tasks.

These guides assume you've read the [Getting Started](../getting-started/index.md)
section and are familiar with the basic [concepts](../concepts/index.md).

## Building Devices

<div class="grid cards" markdown>

-   :material-thermometer:{ .lg .middle .card-icon-right } **Telemetry Device**

    ---

    Build a sensor-polling device with optional publish strategies.

    [:octicons-arrow-right-24: Telemetry Device](telemetry-device.md)

-   :material-thermometer-chevron-up:{ .lg .middle .card-icon-right } **Advanced Telemetry Techniques**

    ---

    On-demand reads, coalescing groups, cron scheduling, and retry/backoff resilience.

    [:octicons-arrow-right-24: Advanced Telemetry Techniques](telemetry-advanced.md)

-   :material-remote:{ .lg .middle .card-icon-right } **Command & Control Device**

    ---

    Build a device that receives commands via MQTT.

    [:octicons-arrow-right-24: Command & Control Device](command-device.md)

-   :material-broadcast:{ .lg .middle .card-icon-right } **Streaming**

    ---

    Stream continuous sensor data from a `StreamablePort` adapter with lifecycle and DI wired automatically.

    [:octicons-arrow-right-24: Streaming](streaming.md)

-   :material-timer-sync:{ .lg .middle .card-icon-right } **Periodic Tasks**

    ---

    Run background coroutines on a fixed interval — flush buffers, send pings, warm caches.

    [:octicons-arrow-right-24: Periodic Tasks](periodic-tasks.md)

-   :material-devices:{ .lg .middle .card-icon-right } **Multi-Device Registration**

    ---

    Register multiple similar devices from settings with `@app.on_configure`
    and dict-name decorators.

    [:octicons-arrow-right-24: Multi-Device Registration](multi-device.md)

</div>

## Structuring an Application

<div class="grid cards" markdown>

-   :material-source-branch:{ .lg .middle .card-icon-right } **Router Composition**

    ---

    Organize multi-module apps with `Router` for testable boundaries.

    [:octicons-arrow-right-24: Router Composition](router-composition.md)

-   :material-link-variant:{ .lg .middle .card-icon-right } **Shared State**

    ---

    Use adapters-as-state to share data between command and telemetry handlers.

    [:octicons-arrow-right-24: Shared State](shared-state.md)

-   :material-timer-sand:{ .lg .middle .card-icon-right } **Lifespan**

    ---

    Run startup and shutdown code with the lifespan context manager.

    [:octicons-arrow-right-24: Lifespan](lifespan.md)

-   :material-puzzle:{ .lg .middle .card-icon-right } **Hardware Adapters**

    ---

    Register adapters: direct, lazy import, dry-run swapping.

    [:octicons-arrow-right-24: Hardware Adapters](hardware-adapters.md)

-   :material-cog:{ .lg .middle .card-icon-right } **Configure Your Application**

    ---

    Extend Settings, use `.env` files, override via CLI.

    [:octicons-arrow-right-24: Configuration](configuration.md)

</div>

## Contracts & Schemas

<div class="grid cards" markdown>

-   :material-file-document-check:{ .lg .middle .card-icon-right } **Contract-First Route Design**

    ---

    Add contract metadata to decorators for machine-readable, auditable interface declarations.

    [:octicons-arrow-right-24: Contract-First Route Design](contract-first-route-design.md)

-   :material-file-check-outline:{ .lg .middle .card-icon-right } **Schema Enforcement**

    ---

    Validate MQTT topics and payloads against an AsyncAPI schema. CI gate and
    fleet-level network schemas.

    [:octicons-arrow-right-24: Schema Enforcement](schema-enforcement.md)

</div>

## Testing & Errors

<div class="grid cards" markdown>

-   :material-test-tube:{ .lg .middle .card-icon-right } **Test Your Application**

    ---

    Use `cosalette.testing`, AppHarness, and pytest fixtures.

    [:octicons-arrow-right-24: Testing](testing.md)

-   :material-alert-outline:{ .lg .middle .card-icon-right } **Custom Error Types**

    ---

    Map domain exceptions to structured error payloads.

    [:octicons-arrow-right-24: Error Types](error-types.md)

</div>

## Operating & Tooling

<div class="grid cards" markdown>

-   :material-docker:{ .lg .middle .card-icon-right } **Containerize Your Application**

    ---

    Package a cosalette app as a Docker image with hardware-specific and multi-arch support.

    [:octicons-arrow-right-24: Containerize](containerize.md)

-   :material-docker:{ .lg .middle .card-icon-right } **Deploy with Docker Compose**

    ---

    Containerise and deploy with Docker, Compose, and Ansible.

    [:octicons-arrow-right-24: Deployment](deployment.md)

-   :material-shield-lock:{ .lg .middle .card-icon-right } **Harden Your Deployment**

    ---

    Security hardening, production logging, and runtime constraints for containerised applications.

    [:octicons-arrow-right-24: Harden](harden.md)

-   :material-bug-outline:{ .lg .middle .card-icon-right } **Troubleshoot a Deployment**

    ---

    Diagnose and fix common problems with containerised cosalette applications.

    [:octicons-arrow-right-24: Troubleshoot a Deployment](troubleshoot-deployment.md)

-   :material-wifi-off:{ .lg .middle .card-icon-right } **Transport Availability**

    ---

    Mark devices offline when transports fail. Standardised availability
    signaling with auto-recovery for SSH, BLE, serial, and HTTP adapters.

    [:octicons-arrow-right-24: Transport Availability](transport-availability.md)

-   :material-transfer:{ .lg .middle .card-icon-right } **Version Migration**

    ---

    Upgrade between cosalette versions — breaking changes, typed payloads,
    Router adoption, testing harness updates.

    [:octicons-arrow-right-24: Version Migration](version-migration.md)

-   :material-robot-outline:{ .lg .middle .card-icon-right } **MCP Server**

    ---

    Expose fourteen structured tools for IDE-native AI agents to query registrations
    and generate idiomatic scaffolding.

    [:octicons-arrow-right-24: MCP Server](mcp-server.md)

</div>
