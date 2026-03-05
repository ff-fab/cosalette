---
icon: material/docker
---

# Deployment

How to containerise and deploy cosalette applications using Docker and Docker Compose.

!!! info "Prerequisites"

    - Docker Engine ≥ 20.10 (with BuildKit)
    - Docker Compose V2
    - [uv](https://docs.astral.sh/uv/) for Python package management

## Dockerfile

A multi-stage Dockerfile that works for most cosalette applications. It uses `uv` for
dependency resolution and produces a minimal runtime image.

```dockerfile title="Dockerfile"
# syntax=docker/dockerfile:1

# ──────────────────────────────────────────────
# Stage 1 — builder
# Resolve dependencies and install the app into
# a virtual environment. Nothing from this stage
# ships in the final image except the venv.
# ──────────────────────────────────────────────
FROM python:3.14-slim AS builder

# Grab the uv binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy dependency metadata first — this layer is
# cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev
# extras). --frozen ensures the lock file is used
# as-is without re-resolving.
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the rest of the source tree and install
# the project itself.
COPY . .
RUN uv sync --frozen --no-dev

# ──────────────────────────────────────────────
# Stage 2 — runtime
# Minimal image with only what the app needs to
# run. No compilers, no build tools, no uv.
# ──────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# Create a non-root user for the application.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

# Copy the virtual environment from the builder.
COPY --from=builder /app/.venv /app/.venv

# Put the venv's bin directory on PATH so the
# console script entry point is directly callable.
ENV PATH="/app/.venv/bin:$PATH"

# Tell Python not to buffer stdout/stderr — logs
# appear immediately in `docker logs`.
ENV PYTHONUNBUFFERED=1

# Use SIGTERM for graceful shutdown. cosalette's
# signal handler catches this and shuts down cleanly.
STOPSIGNAL SIGTERM

USER app

# Replace "myapp" with your console script name
# (the [project.scripts] entry in pyproject.toml).
ENTRYPOINT ["myapp"]
```

!!! tip "Console script vs. module"

    The `ENTRYPOINT` above assumes a console script defined in `pyproject.toml`
    under `[project.scripts]`. If your app uses `__main__.py` instead, change the
    entrypoint to:

    ```dockerfile
    ENTRYPOINT ["python", "-m", "myapp"]
    ```

### Customising for Hardware

IoT applications often need system-level libraries for hardware access. Add the
required packages in the **runtime** stage before switching to the non-root user:

=== "GPIO (libgpiod)"

    ```dockerfile
    RUN apt-get update \
        && apt-get install -y --no-install-recommends libgpiod2 \
        && rm -rf /var/lib/apt/lists/*
    ```

=== "I²C"

    ```dockerfile
    RUN apt-get update \
        && apt-get install -y --no-install-recommends i2c-tools \
        && rm -rf /var/lib/apt/lists/*
    ```

=== "Bluetooth"

    ```dockerfile
    RUN apt-get update \
        && apt-get install -y --no-install-recommends bluez libdbus-1-3 \
        && rm -rf /var/lib/apt/lists/*
    ```

=== "Serial"

    No extra system packages needed — `pyserial` works out of the box. Just make
    sure the container has access to the serial device (see
    [Docker Compose — devices](#docker-compose) below).

## Docker Compose

A reference `docker-compose.yml` for a typical cosalette app running alongside an
MQTT broker.

```yaml title="docker-compose.yml"
services:
  # ── MQTT broker ──────────────────────────────
  mosquitto:
    image: eclipse-mosquitto:2
    restart: unless-stopped
    ports:
      - "1883:1883"
    volumes:
      - mosquitto-config:/mosquitto/config
      - mosquitto-data:/mosquitto/data
      - mosquitto-log:/mosquitto/log

  # ── cosalette application ───────────────────
  myapp:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    depends_on:
      - mosquitto

    environment:
      # ── MQTT ──
      MYAPP_MQTT__HOST: mosquitto          # (1)!
      MYAPP_MQTT__PORT: "1883"
      MYAPP_MQTT__USERNAME: myapp
      MYAPP_MQTT__PASSWORD: changeme
      MYAPP_MQTT__CLIENT_ID: myapp-prod
      MYAPP_MQTT__TOPIC_PREFIX: myapp

      # ── Logging ──
      MYAPP_LOGGING__LEVEL: INFO
      MYAPP_LOGGING__FORMAT: json          # (2)!

      # ── App-specific ──
      # MYAPP_SERIAL_PORT: /dev/ttyUSB0
      # MYAPP_POLL_INTERVAL: "60"

    volumes:
      - app-data:/app/data                 # (3)!

    # ── Hardware devices (uncomment as needed) ──
    # devices:
    #   - /dev/ttyUSB0:/dev/ttyUSB0        # Serial
    #   - /dev/gpiochip0:/dev/gpiochip0    # GPIO
    #   - /dev/i2c-1:/dev/i2c-1            # I²C

volumes:
  mosquitto-config:
  mosquitto-data:
  mosquitto-log:
  app-data:
```

1. Use the **service name** (`mosquitto`) as the hostname — Docker's internal DNS
   resolves it automatically. Never use `localhost` here; that refers to the
   container itself, not the broker.
2. JSON logging is recommended for containers — see [Logging](#logging) below.
3. Mount a volume for persistence stores (`JsonFileStore`, `SqliteStore`). See
   [Persistence](#persistence).

### Environment Variable Reference

All variables use the app's `env_prefix` (here `MYAPP_`) followed by `__` for nested
fields.

#### MQTT Settings

| Variable | Settings Field | Default | Description |
| --- | --- | --- | --- |
| `MYAPP_MQTT__HOST` | `mqtt.host` | `localhost` | MQTT broker hostname |
| `MYAPP_MQTT__PORT` | `mqtt.port` | `1883` | MQTT broker port |
| `MYAPP_MQTT__USERNAME` | `mqtt.username` | `None` | Broker username |
| `MYAPP_MQTT__PASSWORD` | `mqtt.password` | `None` | Broker password |
| `MYAPP_MQTT__CLIENT_ID` | `mqtt.client_id` | `None` | MQTT client identifier |
| `MYAPP_MQTT__TOPIC_PREFIX` | `mqtt.topic_prefix` | app name | Base prefix for all topics |
| `MYAPP_MQTT__RECONNECT_INTERVAL` | `mqtt.reconnect_interval` | `5` | Initial reconnect delay (seconds) |
| `MYAPP_MQTT__RECONNECT_MAX_INTERVAL` | `mqtt.reconnect_max_interval` | `60` | Maximum reconnect delay (seconds) |

#### Logging Settings

| Variable | Settings Field | Default | Description |
| --- | --- | --- | --- |
| `MYAPP_LOGGING__LEVEL` | `logging.level` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MYAPP_LOGGING__FORMAT` | `logging.format` | `json` | Output format (`json` or `text`) |
| `MYAPP_LOGGING__FILE` | `logging.file` | `None` | Log file path (usually unset in containers) |
| `MYAPP_LOGGING__MAX_FILE_SIZE_MB` | `logging.max_file_size_mb` | `10` | Max log file size before rotation |
| `MYAPP_LOGGING__BACKUP_COUNT` | `logging.backup_count` | `3` | Number of rotated log files to keep |

!!! tip "How env var nesting works"

    pydantic-settings maps environment variables to nested models using the
    `env_nested_delimiter`. With `env_nested_delimiter="__"` and
    `env_prefix="MYAPP_"`:

    ```text
    MYAPP_MQTT__HOST=broker.local
           ^^^^ ^^^^
           │    └─ field name on MqttSettings
           └────── sub-model name on Settings
    ```

    The framework's base `Settings` declares `mqtt: MqttSettings` and
    `logging: LoggingSettings`, so the `MQTT__` and `LOGGING__` segments route to
    those sub-models. Your own flat fields (like `MYAPP_POLL_INTERVAL`) have no
    double-underscore and map directly to top-level settings.

    Because `Settings` is configured with `extra="ignore"`, any environment variable
    that doesn't match a known field is silently skipped — no validation errors from
    unrelated system env vars.

## Multi-Architecture Builds

Both the Raspberry Pi 4 and Raspberry Pi Zero 2 W use **arm64** (aarch64), so a
single image target covers both boards.

### Cross-building from an amd64 dev machine

Use Docker BuildKit with `buildx` to cross-compile:

```bash
# One-time setup: create a builder with QEMU support
docker buildx create --name pibuilder --use
docker buildx inspect --bootstrap

# Build and push a multi-arch image
docker buildx build \
    --platform linux/arm64 \
    --tag registry.example.com/myapp:latest \
    --push \
    .
```

!!! note "QEMU emulation"

    `docker buildx` uses QEMU under the hood for cross-platform builds. On most
    Docker Desktop and modern Linux installations, QEMU user-mode emulation is
    already configured. If not, enable it with:

    ```bash
    docker run --privileged --rm tonistiigi/binfmt --install arm64
    ```

!!! warning "Pi Zero 2 W memory constraints"

    The Pi Zero 2 W has only **512 MB RAM**. Keep your images lean:

    - Use `python:3.14-slim` (not the full image).
    - Avoid heavy dependencies where possible.
    - Set `MYAPP_LOGGING__LEVEL=WARNING` in production to reduce log volume.
    - Prefer `MemoryStore` or `NullStore` over `SqliteStore` if persistence isn't
      critical — SQLite's page cache can be memory-hungry on constrained devices.

### Building natively on the Pi

If you're building directly on a Pi 4 (which has 4–8 GB RAM), a standard
`docker build` works without any special flags:

```bash
docker build -t myapp:latest .
```

Avoid building on the Pi Zero 2 W — its limited RAM makes builds unreliable.
Cross-build on a dev machine or CI instead.

## Health Checks

cosalette uses **MQTT-native health reporting**
([ADR-012](../adr/ADR-012-health-and-availability-reporting.md)) rather than an HTTP
health endpoint. The framework publishes a structured JSON heartbeat to
`{prefix}/status` and configures an MQTT Last Will and Testament (LWT) so the broker
automatically publishes an `"offline"` message if the client disconnects unexpectedly.

### Why no HTTP health endpoint?

cosalette applications are **pure MQTT daemons** — adding an HTTP server solely for
health checks would increase the attack surface, add dependencies, and consume
resources on constrained devices. ADR-012 explicitly rejected this approach.

### MQTT-based health check

If `mosquitto_sub` is available in the container, you can use it to verify the app's
MQTT heartbeat:

```yaml title="docker-compose.yml (health check snippet)"
services:
  myapp:
    # ...
    healthcheck:
      test: >-
        mosquitto_sub
        -h mosquitto
        -t "myapp/status"
        -C 1
        -W 30
      interval: 60s
      timeout: 35s
      retries: 3
      start_period: 15s
```

This subscribes to the status topic, waits up to 30 seconds (`-W`) for a single
message (`-C 1`), and exits 0 if one is received. You'll need `mosquitto-clients`
installed in the runtime image:

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends mosquitto-clients \
    && rm -rf /var/lib/apt/lists/*
```

### Process-based fallback

If you'd rather not add `mosquitto-clients` to the image, a simple process check
works as a basic health signal:

```yaml title="docker-compose.yml (process health check)"
services:
  myapp:
    # ...
    healthcheck:
      test: ["CMD", "pgrep", "-f", "myapp"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

!!! info "LWT handles crash detection automatically"

    Even without a Docker `HEALTHCHECK`, the MQTT broker publishes the LWT `"offline"`
    message to `{prefix}/status` when the client TCP connection drops. Downstream
    consumers (like Home Assistant) detect the outage without any polling.

## Logging

### Use JSON format in containers

Set `--log-format json` (or the `MYAPP_LOGGING__FORMAT=json` env var) for structured
NDJSON output. This is the recommended format for containerised deployments:

```json
{"timestamp":"2026-03-05T10:15:30.123Z","level":"INFO","message":"Connected to broker","host":"mosquitto","port":1883}
```

Docker's default `json-file` log driver wraps each line in its own JSON envelope, so
structured log lines are preserved as single entries.

### Let Docker handle log rotation

In a container, **do not** set `MYAPP_LOGGING__FILE` — write to stdout/stderr and let
the Docker daemon manage rotation:

```json title="/etc/docker/daemon.json"
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

This applies globally to all containers. You can also set `logging:` per-service in
`docker-compose.yml`:

```yaml title="docker-compose.yml (logging snippet)"
services:
  myapp:
    # ...
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

### Log aggregation

For fleet-wide observability, forward container logs to a centralised system.
[Grafana Loki](https://grafana.com/oss/loki/) with Promtail is a lightweight option
that works well on Raspberry Pi hardware. Configure the Loki Docker logging driver or
run Promtail as a sidecar container that tails the Docker log files.

### Viewing logs

```bash
# Last 20 log entries
docker logs --tail 20 myapp

# Follow live output
docker logs -f myapp

# Filter structured logs with jq
docker logs myapp 2>&1 | jq 'select(.level == "ERROR")'
```

## Persistence

`JsonFileStore` and `SqliteStore` write to disk and need a mounted volume to survive
container restarts. `MemoryStore` and `NullStore` are ephemeral and need no volume.

### Volume mount

```yaml title="docker-compose.yml (persistence snippet)"
services:
  myapp:
    # ...
    volumes:
      - app-data:/app/data

volumes:
  app-data:
```

Configure your store path to write inside the mounted directory (e.g.,
`/app/data/state.json` or `/app/data/store.sqlite`).

### Permissions

The Dockerfile creates a non-root user (`app`, UID 1000). If the named volume is
freshly created, Docker sets ownership automatically. For bind mounts, ensure the
host directory is writable by UID 1000:

```bash
mkdir -p ./data
chown 1000:1000 ./data
```

## Graceful Shutdown

cosalette installs signal handlers for `SIGTERM` and `SIGINT`. When Docker sends
`SIGTERM` (via `docker stop` or Compose shutdown), the framework:

1. Cancels all running device tasks.
2. Publishes `"offline"` to per-device availability topics.
3. Flushes persistence stores.
4. Publishes a final status update to `{prefix}/status`.
5. Disconnects from the MQTT broker cleanly.

The `STOPSIGNAL SIGTERM` directive in the Dockerfile ensures Docker sends the right
signal. The default `stop_grace_period` of 10 seconds in Compose is usually
sufficient. Increase it if your app has slow cleanup (e.g., large store flushes):

```yaml title="docker-compose.yml (grace period snippet)"
services:
  myapp:
    # ...
    stop_grace_period: 30s
```

!!! info "LWT as a safety net"

    If the process is killed hard (OOM, `docker kill`, power loss), the MQTT broker
    publishes the pre-configured LWT `"offline"` message. The graceful shutdown
    path and the LWT path converge on the same outcome — downstream consumers always
    see an `"offline"` status.

## Ansible Deployment

Ansible is a natural fit for deploying Compose-based applications to a fleet of
Raspberry Pis. The general pattern: template the `docker-compose.yml` with Jinja2,
copy it to each host, and let Compose manage the containers.

!!! note

    Ansible playbooks are infrastructure-level tooling — outside the scope of the
    cosalette framework itself. This section provides a starting point, not a
    complete Ansible role.

### Jinja2 template

```yaml title="templates/docker-compose.yml.j2"
services:
  mosquitto:
    image: eclipse-mosquitto:2
    restart: unless-stopped
    ports:
      - "1883:1883"
    volumes:
      - mosquitto-data:/mosquitto/data

  {{ app_name }}:
    image: "{{ docker_registry }}/{{ app_name }}:{{ app_version }}"
    restart: unless-stopped
    depends_on:
      - mosquitto
    environment:
      {{ env_prefix }}_MQTT__HOST: mosquitto
      {{ env_prefix }}_MQTT__USERNAME: "{{ mqtt_username }}"
      {{ env_prefix }}_MQTT__PASSWORD: "{{ mqtt_password }}"
      {{ env_prefix }}_MQTT__TOPIC_PREFIX: "{{ topic_prefix }}"
      {{ env_prefix }}_LOGGING__LEVEL: "{{ log_level | default('INFO') }}"
      {{ env_prefix }}_LOGGING__FORMAT: json
{% if serial_device is defined %}
    devices:
      - {{ serial_device }}:{{ serial_device }}
{% endif %}
    volumes:
      - app-data:/app/data

volumes:
  mosquitto-data:
  app-data:
```

### Playbook snippet

```yaml title="deploy.yml"
- name: Deploy cosalette app
  hosts: pis
  tasks:
    - name: Create app directory
      ansible.builtin.file:
        path: "/opt/{{ app_name }}"
        state: directory
        mode: "0755"

    - name: Template docker-compose.yml
      ansible.builtin.template:
        src: templates/docker-compose.yml.j2
        dest: "/opt/{{ app_name }}/docker-compose.yml"
        mode: "0644"

    - name: Pull and start services
      community.docker.docker_compose_v2:
        project_src: "/opt/{{ app_name }}"
        pull: always
        state: present
```

Define per-host variables in your Ansible inventory to customise each deployment
(broker credentials, serial devices, topic prefixes, etc.).

## Troubleshooting

**Container starts but no MQTT connection**
:   The broker hostname must be the Compose **service name** (e.g., `mosquitto`),
    not `localhost`. Inside a container, `localhost` refers to the container itself.
    Verify with `docker exec myapp ping mosquitto`.

**Permission denied on `/dev/ttyUSB0`**
:   The container needs access to the host device. Options:

    1. Add `device_cgroup_rules: ['c 188:* rmw']` under the service.
    2. Use `privileged: true` (less secure, but simple for development).
    3. Add the container user to the `dialout` group.

**Out of memory on Pi Zero 2 W**
:   The Pi Zero 2 W has only 512 MB RAM. To reduce memory usage:

    - Set `MYAPP_LOGGING__LEVEL=WARNING` to reduce log buffer pressure.
    - Use `MemoryStore` or `NullStore` instead of `SqliteStore`.
    - Run `docker system prune` to reclaim space from old images.
    - Consider adding a swap file on the host.

**Container restarts in a loop**
:   Check the exit code with `docker inspect --format='{{.State.ExitCode}}' myapp`:

    | Exit Code | Meaning | Action |
    | --- | --- | --- |
    | `1` | Configuration error | Check env vars — missing required field, invalid value |
    | `3` | Runtime error | Check logs with `docker logs myapp` for the root cause |
    | `137` | OOM killed / SIGKILL | Increase memory limit or reduce footprint |

**Image fails to build for arm64**
:   Ensure BuildKit and QEMU are set up:

    ```bash
    docker run --privileged --rm tonistiigi/binfmt --install arm64
    docker buildx create --name pibuilder --use
    ```
