---
icon: material/hammer-wrench
---

# How-To Guides

Step-by-step instructions for common tasks.

These guides assume you've read the [Getting Started](../getting-started/index.md)
section and are familiar with the basic [concepts](../concepts/index.md).

<div class="grid cards" markdown>

-   :material-thermometer:{ .lg .middle .card-icon-right } **Telemetry Device**

    ---

    Build a sensor-polling device with optional publish strategies.

    [:octicons-arrow-right-24: Telemetry Device](telemetry-device.md)

-   :material-remote:{ .lg .middle .card-icon-right } **Command & Control Device**

    ---

    Build a device that receives commands via MQTT.

    [:octicons-arrow-right-24: Command Device](command-device.md)

-   :material-source-branch:{ .lg .middle .card-icon-right } **Router Composition**

    ---

    Organize multi-module apps with `Router` for testable boundaries.

    [:octicons-arrow-right-24: Router Composition](router-composition.md)

-   :material-cog:{ .lg .middle .card-icon-right } **Configuration**

    ---

    Extend Settings, use `.env` files, override via CLI.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   :material-puzzle:{ .lg .middle .card-icon-right } **Hardware Adapters**

    ---

    Register adapters: direct, lazy import, dry-run swapping.

    [:octicons-arrow-right-24: Adapters](adapters.md)

-   :material-link-variant:{ .lg .middle .card-icon-right } **Share State Between Handlers**

    ---

    Use adapters-as-state to share data between command and telemetry handlers.

    [:octicons-arrow-right-24: Shared State](shared-state.md)

-   :material-timer-sand:{ .lg .middle .card-icon-right } **Lifespan**

    ---

    Run startup and shutdown code with the lifespan context manager.

    [:octicons-arrow-right-24: Lifespan](lifespan.md)

-   :material-devices:{ .lg .middle .card-icon-right } **Multi-Device Registration**

    ---

    Register multiple similar devices from settings with `@app.on_configure`
    and dict-name decorators.

    [:octicons-arrow-right-24: Multi-Device Registration](multi-device.md)

-   :material-test-tube:{ .lg .middle .card-icon-right } **Testing**

    ---

    Use `cosalette.testing`, AppHarness, and pytest fixtures.

    [:octicons-arrow-right-24: Testing](testing.md)

-   :material-alert-outline:{ .lg .middle .card-icon-right } **Custom Error Types**

    ---

    Map domain exceptions to structured error payloads.

    [:octicons-arrow-right-24: Error Types](error-types.md)

-   :material-docker:{ .lg .middle .card-icon-right } **Deployment**

    ---

    Containerise and deploy with Docker, Compose, and Ansible.

    [:octicons-arrow-right-24: Deployment](deployment.md)

-   :material-file-check-outline:{ .lg .middle .card-icon-right } **Schema Enforcement**

    ---

    Validate MQTT topics and payloads against an AsyncAPI schema. CI gate and
    fleet-level network schemas.

    [:octicons-arrow-right-24: Schema Enforcement](schema-enforcement.md)

-   :material-rocket-launch:{ .lg .middle .card-icon-right } **Build a Full App** :material-star:{ .star }

    ---

    Capstone guide — combines everything above into a complete application.

    [:octicons-arrow-right-24: Full App Guide](full-app.md)

-   :material-transfer:{ .lg .middle .card-icon-right } **Version Migration**

    ---

    Upgrade between cosalette versions — breaking changes, typed payloads,
    Router adoption, testing harness updates.

    [:octicons-arrow-right-24: Version Migration](migrate-legacy-app.md)

</div>
