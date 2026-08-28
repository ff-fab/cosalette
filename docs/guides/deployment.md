---
icon: material/server-network
---

# Deploy with Docker Compose

How to deploy a containerised cosalette application using Docker Compose, including
health checks, persistence, graceful shutdown, and Ansible-based fleet rollouts.

!!! info "Prerequisites"

    - Docker Engine ≥ 20.10 (with BuildKit)
    - Docker Compose V2
    - A built image — see [Containerize Your Application](containerize.md)

## Docker Compose

A reference `docker-compose.yml` for a typical cosalette app running alongside an
MQTT broker.

```yaml title="docker-compose.yml"
services:
  # ── MQTT broker ──────────────────────────────
  mosquitto:
    image: eclipse-mosquitto:2
    restart: unless-stopped
    # Development-only: bind to localhost so the broker is not exposed to
    # the LAN/Internet. For production, configure authentication and TLS
    # in mosquitto.conf and expose only a TLS listener (e.g. 8883).
    ports:
      - "127.0.0.1:1883:1883"
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

    # ── Runtime hardening (safe defaults; see "Harden Your Deployment") ──
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: 256m
    cpus: 1.0
    tmpfs:
      - /tmp   # writable scratch on the read-only root filesystem

    environment:
      # ── MQTT ──
      MYAPP_MQTT__HOST: mosquitto          # (1)!
      MYAPP_MQTT__PORT: "1883"
      MYAPP_MQTT__USERNAME: myapp
      # Never hard-code a broker password. Provide it at deploy time from an
      # untracked .env file (Compose reads .env automatically) or, better, a
      # Docker secret via MYAPP_MQTT__PASSWORD_FILE — see "Secrets management".
      MYAPP_MQTT__PASSWORD: ${MYAPP_MQTT_PASSWORD:?set MYAPP_MQTT_PASSWORD in your .env}
      MYAPP_MQTT__CLIENT_ID: myapp-prod
      MYAPP_MQTT__TOPIC_PREFIX: myapp
      # TLS is on by default since 0.7.0 (ADR-062). The mosquitto service
      # above listens on plain 1883 (no TLS), so this example opts out
      # explicitly. For production, terminate TLS on the broker (e.g. an
      # 8883 listener) and remove this override plus the two lines below it.
      MYAPP_MQTT__TLS: "false"
      # When connecting to a TLS listener such as 8883, drop the override
      # above and set the CA bundle instead:
      # MYAPP_MQTT__TLS_CA_FILE: /run/secrets/mqtt-ca.pem

      # ── Logging ──
      MYAPP_LOGGING__LEVEL: INFO
      MYAPP_LOGGING__FORMAT: json          # (2)!

      # ── App-specific ──
      # MYAPP_SERIAL_PORT: /dev/ttyUSB0
      # MYAPP_POLL_INTERVAL: "60"

    volumes:
      - app-data:/app/data                 # (3)!

    # ── Hardware devices (uncomment as needed) ──
    # Prefer scoped device access below over privileged: true (see Troubleshoot a Deployment).
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
2. JSON logging is recommended for containers — see [Logging](harden.md#logging).
3. Mount a volume for persistence stores (`JsonFileStore`, `SqliteStore`). See
   [Persistence](#persistence).

### Production MQTT Hardening

The Compose example above is intentionally local-development friendly: the broker
binds plaintext MQTT to `127.0.0.1` on the host and only the app reaches it over
the Docker network. For production, harden the broker before exposing it beyond a
single trusted host.

- Require named users; keep `allow_anonymous false`.
- Give each app its own MQTT username and ACL scoped to its topic prefix.
- Expose plaintext port `1883` only on localhost or private Docker networks.
- Use TLS on port `8883` for traffic crossing hosts, VLANs, or untrusted networks.
- `MYAPP_MQTT__TLS` defaults to `true` since 0.7.0 (ADR-062) — set
  `MYAPP_MQTT__TLS_CA_FILE=/path/to/ca.pem` when connecting to a TLS listener
  with a private CA. Brokers without a TLS listener (like the local-dev
  `mosquitto` service above) need an explicit `MYAPP_MQTT__TLS=false`.
- Avoid shared credentials across devices; rotate passwords when hardware is
  retired or transferred.
- Treat retained topics as persisted data: publish only values you are willing to
  leave visible to subscribers with matching ACLs.

A minimal Mosquitto production listener looks like this:

```conf title="mosquitto.conf"
allow_anonymous false
password_file /mosquitto/config/passwords
acl_file /mosquitto/config/acl

listener 8883
cafile /mosquitto/config/ca.pem
certfile /mosquitto/config/server.crt
keyfile /mosquitto/config/server.key
```

```conf title="acl"
user myapp
topic readwrite myapp/#
```

If you use mutual TLS, also set `MYAPP_MQTT__TLS_CERT_FILE` and
`MYAPP_MQTT__TLS_KEY_FILE` so cosalette can load the client certificate chain.

#### Retained topic trust

Retained `{prefix}/{device}/state`/`{prefix}/{device}/availability` topics are
writable by **any** publisher the broker admits: MQTT has no integrity
protection, so any broker-admitted client can overwrite them with arbitrary
payloads that downstream consumers will treat as authoritative state. Per-app
prefix ACLs are therefore mandatory for **integrity**, not just privacy — scope
each app user to its own prefix and keep observers read-only:

```conf title="acl"
# The app may read/write only its own prefix
user myapp
topic readwrite myapp/#

# Observers (dashboards, integrations) get read-only access
user observer
topic read myapp/#
```

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
| `MYAPP_MQTT__TLS` | `mqtt.tls` | `true` | Enable TLS client connection. Defaults to `true` since 0.7.0 (ADR-062); set `false` for brokers without TLS support |
| `MYAPP_MQTT__TLS_CA_FILE` | `mqtt.tls_ca_file` | `None` | CA bundle for broker certificate validation |
| `MYAPP_MQTT__TLS_CERT_FILE` | `mqtt.tls_cert_file` | `None` | Client certificate for mutual TLS |
| `MYAPP_MQTT__TLS_KEY_FILE` | `mqtt.tls_key_file` | `None` | Client private key for mutual TLS |
| `MYAPP_MQTT__CLIENT_ID` | `mqtt.client_id` | `""` (auto-generated) | MQTT client identifier |
| `MYAPP_MQTT__TOPIC_PREFIX` | `mqtt.topic_prefix` | `""` (falls back to app name) | Base prefix for all topics |
| `MYAPP_MQTT__RECONNECT_INTERVAL` | `mqtt.reconnect_interval` | `5` | Initial reconnect delay (seconds) |
| `MYAPP_MQTT__RECONNECT_MAX_INTERVAL` | `mqtt.reconnect_max_interval` | `300` | Maximum reconnect delay (seconds) |

#### Logging Settings

| Variable | Settings Field | Default | Description |
| --- | --- | --- | --- |
| `MYAPP_LOGGING__LEVEL` | `logging.level` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
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

## Updating the Deployment

A deployed Compose stack stays current through a small maintenance loop:

- **Pin the app image by digest.** Reference the image as
  `myapp@sha256:...` rather than a mutable tag for reproducible rollouts —
  [Containerize Your Application](containerize.md) recommends this by default.
- **Pull on a schedule.** Run `docker compose pull && docker compose up -d`
  from a cron job or systemd timer so new images are picked up automatically.
- **Re-scan the deployed image.** CI scans catch build-time vulnerabilities;
  re-scan periodically with Trivy to catch advisories published after deploy:

  ```bash
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
      aquasec/trivy:0.59.2 image --severity HIGH,CRITICAL myapp
  ```

- **Check broker config drift.** Verify the `mosquitto.conf` and ACL files in
  the mounted config volume still match your intended listeners, credentials,
  and ACLs (diff them against your templated source of truth) — drift here can
  silently widen broker access.

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

## Persistence

`JsonFileStore` and `SqliteStore` write to disk and need a mounted volume to survive
container restarts. `MemoryStore` and `NullStore` are ephemeral and need no volume.

### Zero-config default store

When `store=` is omitted, the framework resolves a `JsonFileStore` automatically
(ADR-049). Inside a container, the default path (`$XDG_STATE_HOME/<name>/store.json`,
typically `~/.local/state/<name>/store.json`) lives on the container's ephemeral
filesystem. Same-boot entity-removal cleanup still works from an ephemeral store,
but **cross-restart cleanup requires a durable path**.

!!! warning "Startup WARNING in containers"
    The framework logs a `WARNING` at startup when **all three** conditions hold:
    the auto-resolved default store is in use (no explicit `store=`/`store=None`),
    a container runtime is detected, and `<NAME>_STORE_PATH` is not set — **and**
    the app's entity set may vary by config (any `device`/`telemetry`/`command`
    uses a callable `name=` or `enabled=`, or the app has `@app.on_configure`
    hooks).

    Apps with a fixed static entity set (static string names, literal `enabled=`,
    no `on_configure` hooks) do **not** warn — they have nothing for ADR-048
    cleanup to recover across restarts. Such apps no longer need `store=None`
    purely to silence a false-positive warning. They also skip the snapshot
    write entirely: no `store.json` is created unless `persist=` is also used.

    For an `@app.on_configure` app that uses the hook for non-entity-varying
    reasons (e.g. config validation only), pass `retained_cleanup=False` to
    silence the warning explicitly without giving up persistence — unlike
    `store=None`, the store is kept for `persist=` device state; only ADR-048
    cleanup and the warning are disabled.

To make the default store durable, set the `<NAME>_STORE_PATH` environment variable
to a path on a mounted volume:

```yaml title="docker-compose.yml (persistence snippet)"
services:
  airthings2mqtt:
    # ...
    environment:
      AIRTHINGS2MQTT_STORE_PATH: /app/data/store.json   # (1)!
    volumes:
      - app-data:/app/data

volumes:
  app-data:
```

1. App name upper-cased with all non-alphanumeric characters replaced by underscores,
   followed by `_STORE_PATH` (e.g. `sensor.hub` → `SENSOR_HUB_STORE_PATH`).

#### High-write apps: SqliteStore default

For apps with frequent store writes, swap the auto-resolved backend to `SqliteStore`
once at startup:

```python
import cosalette
from cosalette import SqliteStore

cosalette.set_default_store_backend(SqliteStore)

app = cosalette.App(name="myapp", version="1.0.0")
```

The path is still resolved from `<NAME>_STORE_PATH` or the XDG default — only the
backend format changes. Call `set_default_store_backend()` before constructing any
`App()` instances. Explicit `store=` arguments are unaffected.

!!! note "Eager database open"
    Unlike the lazy `JsonFileStore` default (which does no I/O until the first
    save), a `SqliteStore` default opens the database eagerly at `App(...)`
    construction time — creating parent directories and opening the connection
    immediately.

!!! warning "Switching from an existing JsonFileStore"
    `SqliteStore` and `JsonFileStore` use different file formats. If
    `store.json` already exists at the default path and you switch to
    `SqliteStore`, the open will fail with "file is not a database".
    Set `<NAME>_STORE_PATH` to a new filename (e.g.
    `MYAPP_STORE_PATH=/app/data/store.sqlite3`) or migrate/delete the
    existing file first.

### Explicit store path

For apps that configure `store=` explicitly (via a `JsonFileStore` or factory),
configure the path to write inside a mounted directory (e.g.,
`/app/data/state.json` or `/app/data/store.sqlite`):

### Volume mount

```yaml title="docker-compose.yml (explicit store snippet)"
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
      - "127.0.0.1:1883:1883"
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

---

**Related guides:**

- [Containerize Your Application](containerize.md) — Dockerfile and multi-arch builds
- [Harden Your Deployment](harden.md) — security hardening, image scanning, and production logging
- [Troubleshoot a Deployment](troubleshoot-deployment.md) — diagnosing common problems
