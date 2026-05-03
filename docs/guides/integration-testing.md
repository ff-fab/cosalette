---
icon: material/test-tube-empty
---

# Run Integration Tests Manually

How to run cosalette integration tests locally and trigger them manually in CI.

!!! info "Prerequisites"

    - Python environment set up via `uv` (see [Getting Started](../getting-started/quickstart.md))
    - [Docker](https://docs.docker.com/get-docker/) Engine ≥ 20.10 — **required for MQTT tests only**

## Test Suites at a Glance

cosalette integration tests are split into two suites by pytest marker:

| Suite | Marker | Requires Docker | Runs on PR / push |
| ----- | ------ | --------------- | ----------------- |
| **Fast integration** | `integration and not mqtt` | No | Yes |
| **MQTT integration** | `mqtt` | Yes (Mosquitto via testcontainers) | No |

MQTT tests are excluded from PR, push, and `task pre-pr` gates because they require a
running Docker daemon and add significant wall-clock time. They are run manually (locally
or via CI workflow dispatch) and as the Release Please full-suite gate before TestPyPI.

---

## Run Fast Integration Tests

Fast integration tests cover the full application lifecycle using `AppHarness` and
in-memory doubles. No external services are needed.

```bash
task test:integration
```

This runs all files under `packages/tests/integration/` matching
`-m "integration and not mqtt"`.

---

## Run MQTT Integration Tests

MQTT tests spin up a real Mosquitto broker in a Docker container using
[testcontainers](https://testcontainers.com/). Docker must be running before
invoking the task.

### Prerequisites

1. Start Docker Engine (or Docker Desktop).
2. Verify Docker is available:

    ```bash
    docker info
    ```

### Run

```bash
task test:mqtt
```

This runs `packages/tests/integration/test_mqtt_integration.py` with marker `-m mqtt`.

The first run pulls the Mosquitto image; subsequent runs use the local cache.

---

## Run the Full Integration Suite

To run fast integration tests **and** MQTT Docker tests together:

```bash
task test:integration:full
```

!!! warning "Docker required"

    `task test:integration:full` starts Docker containers for MQTT. Ensure Docker Engine
    is running before executing this task.

---

## Trigger Integration Tests in CI

The repository includes a manually dispatchable workflow at
`.github/workflows/integration-tests.yml`.

### Dispatch from the GitHub UI

1. Open the repository on GitHub.
2. Navigate to **Actions → Integration Tests**.
3. Click **Run workflow**.
4. Select the `suite` input:

    | `suite` value | What runs |
    | ------------- | --------- |
    | `mqtt`        | MQTT tests only |
    | `full`        | Fast integration + MQTT Docker tests |

5. Click **Run workflow** to confirm.

### Dispatch via GitHub CLI

```bash
gh workflow run integration-tests.yml -f suite=mqtt
# or
gh workflow run integration-tests.yml -f suite=full
```

---

## Why MQTT Tests Are Not in the Default CI Gate

Regular CI (PR and push) runs `task ci:test:integration`, which uses marker
`integration and not mqtt`. This keeps CI fast and free of Docker dependencies.

MQTT tests require:

- A Docker daemon (not always available in all CI runners)
- Additional setup time to pull and start the Mosquitto container
- Network-level isolation that complicates parallel job execution

Running them on demand keeps the fast gate fast while still providing full
broker-level coverage when needed.
