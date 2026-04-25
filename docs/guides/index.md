---
icon: material/hammer-wrench
---

# How-To Guides

Step-by-step instructions for common tasks.

These guides assume you've read the [Getting Started](../getting-started/index.md)
section and are familiar with the basic [concepts](../concepts/index.md).

<div class="grid cards" markdown>

-   :material-thermometer:{ .lg .middle } **Telemetry Device**

    ---

    Build a sensor-polling device with optional publish strategies.

    [:octicons-arrow-right-24: Telemetry Device](telemetry-device.md)

-   :material-remote:{ .lg .middle } **Command & Control Device**

    ---

    Build a device that receives commands via MQTT.

    [:octicons-arrow-right-24: Command Device](command-device.md)

-   :material-cog:{ .lg .middle } **Configuration**

    ---

    Extend Settings, use `.env` files, override via CLI.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   :material-puzzle:{ .lg .middle } **Hardware Adapters**

    ---

    Register adapters: direct, lazy import, dry-run swapping.

    [:octicons-arrow-right-24: Adapters](adapters.md)

-   :material-link-variant:{ .lg .middle } **Share State Between Handlers**

    ---

    Use adapters-as-state to share data between command and telemetry handlers.

    [:octicons-arrow-right-24: Shared State](shared-state.md)

-   :material-timer-sand:{ .lg .middle } **Lifespan**

    ---

    Run startup and shutdown code with the lifespan context manager.

    [:octicons-arrow-right-24: Lifespan](lifespan.md)

-   :material-devices:{ .lg .middle } **Multi-Device Registration**

    ---

    Register multiple similar devices from settings with `@app.on_configure`
    and dict-name decorators.

    [:octicons-arrow-right-24: Multi-Device Registration](multi-device.md)

-   :material-test-tube:{ .lg .middle } **Testing**

    ---

    Use `cosalette.testing`, AppHarness, and pytest fixtures.

    [:octicons-arrow-right-24: Testing](testing.md)

-   :material-alert-outline:{ .lg .middle } **Custom Error Types**

    ---

    Map domain exceptions to structured error payloads.

    [:octicons-arrow-right-24: Error Types](error-types.md)

-   :material-docker:{ .lg .middle } **Deployment**

    ---

    Containerise and deploy with Docker, Compose, and Ansible.

    [:octicons-arrow-right-24: Deployment](deployment.md)

-   :material-file-check-outline:{ .lg .middle } **Schema Enforcement**

    ---

    Validate MQTT topics and payloads against an AsyncAPI schema. CI gate and
    fleet-level network schemas.

    [:octicons-arrow-right-24: Schema Enforcement](schema-enforcement.md)

-   :material-rocket-launch:{ .lg .middle } **Build a Full App** :material-star:{ .star }

    ---

    Capstone guide — combines everything above into a complete application.

    [:octicons-arrow-right-24: Full App Guide](full-app.md)

-   :material-transfer:{ .lg .middle } **Migrate a Legacy App with AI Agents**

    ---

    Build a new cosalette project using an existing IoT app as a
    specification, guided by AI-assisted development tools.

    [:octicons-arrow-right-24: Legacy Migration](migrate-legacy-app.md)

</div>
