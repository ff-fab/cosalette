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
      # Retained-message expiry is opt-in and requires an MQTT 5-capable broker.
      # These settings are read at client start; restart the app after changing them.
      # MYAPP_MQTT__PROTOCOL_VERSION: "5"
      # MYAPP_MQTT__MESSAGE_EXPIRY_INTERVAL: "86400"

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
| `MYAPP_MQTT__PROTOCOL_VERSION` | `mqtt.protocol_version` | `3.1.1` | Set `5` to enable retained-message expiry on an MQTT 5-capable broker |
| `MYAPP_MQTT__MESSAGE_EXPIRY_INTERVAL` | `mqtt.message_expiry_interval` | `86400` | Retained-message and WILL expiry in seconds under MQTT 5 (minimum `3`) |

MQTT 5 expiry uses an in-process ledger to republish retained messages every third
of the configured interval. It is limited to 1,000 distinct retained topics and
16 MiB of UTF-8 topic and payload data; a publish beyond either limit is rejected with
`RuntimeError` until an existing topic is cleared or retained payload data is reduced.
Protocol and expiry values are captured at client start, so deploy a restart after
changing either setting.

#### Logging Settings

| Variable | Settings Field | Default | Description |
| --- | --- | --- | --- |
| `MYAPP_LOGGING__LEVEL` | `logging.level` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `MYAPP_LOGGING__FORMAT` | `logging.format` | `json` | Output format (`json` or `text`) |
| `MYAPP_LOGGING__FILE` | `logging.file` | `None` | Log file path (usually unset in containers) |
| `MYAPP_LOGGING__MAX_FILE_SIZE_MB` | `logging.max_file_size_mb` | `10` | Max log file size before rotation |
| `MYAPP_LOGGING__BACKUP_COUNT` | `logging.backup_count` | `3` | Number of rotated log files to keep |

#### Framework Variables

These per-deployment switches are not settings fields and carry no app prefix.

| Variable | Default | Description |
| --- | --- | --- |
| `COSALETTE_HEALTH_FILE` | unset (off) | Path of the opt-in health file for a container probe ([Probe for orchestrators](#probe-for-orchestrators-that-act-on-it)) |
| `COSALETTE_LOOP_STALL_TIMEOUT` | unset (off) | Seconds without the event loop before the process exits with code `6`; must be a positive number ([Loop-stall watchdog](#loop-stall-watchdog)) |

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
health endpoint. The recommended setup has two parts: watch the MQTT signals the
app already publishes, and let the app exit when it cannot recover so a restart
policy starts it again
([ADR-083](../adr/ADR-083-opt-in-health-file-and-a-health-cli-probe-for-container-liveness.md)).
A container health probe is optional and only worth running where an orchestrator
acts on it.

!!! tip "Unhealthy does not mean restarted"

    Plain Docker and Docker Compose only *mark* a container unhealthy; they never
    restart it. A `HEALTHCHECK` there changes the status shown by `docker ps` and
    nothing else. Recovery comes from the app exiting and the restart policy
    starting it again, as described under [Supervised restart](#supervised-restart).

### MQTT signals

| Topic | Says | Published by |
| ----- | ---- | ------------ |
| `{prefix}/status` | The app is alive: a retained JSON heartbeat every `heartbeat_interval` (60 s by default), with a per-device `status` | The app |
| `{prefix}/status` = `"offline"` | The app stopped or lost its connection | The app on a clean shutdown; the broker (LWT) on a crash, hang or network loss |
| `{prefix}/{device}/availability` | Each entity's data is fresh: `"online"` / `"offline"` (root telemetry uses `{prefix}/availability`; root streams have no availability topic) | The app |

A telemetry entity, or a stream with `stale_after=`, that delivers no fresh data
for its `stale_after` bound goes `"offline"` on its availability topic and
reports `stale` in the heartbeat. Root streams report staleness only in the
heartbeat and optional health file, without changing MQTT availability
([Staleness](../concepts/staleness.md)).
Alert on:

- `{prefix}/status` staying `"offline"`, or no heartbeat arriving for about three
  `heartbeat_interval`s;
- an availability topic staying `"offline"`, or a heartbeat device entry staying
  `stale`, for longer than you can tolerate missing data.

Wait a few minutes before alerting: each restart publishes `"offline"` briefly.

!!! info "LWT handles crash detection automatically"

    The broker publishes the LWT `"offline"` message to `{prefix}/status` when the
    client connection drops, including when a blocked event loop stops sending
    keepalives. Downstream consumers (like Home Assistant) detect the outage
    without any polling or probe.

#### Why no HTTP health endpoint?

cosalette applications are **pure MQTT daemons** — adding an HTTP server solely for
health checks would increase the attack surface, add dependencies, and consume
resources on constrained devices. ADR-012 explicitly rejected this approach.

### Supervised restart

Let the app exit when it is stuck and the restart policy start it again:

```yaml title="docker-compose.yml (supervised restart)"
services:
  myapp:
    # ...
    restart: unless-stopped
    environment:
      COSALETTE_LOOP_STALL_TIMEOUT: "300"  # exit 6 after 5 min without the event loop
```

```python title="app.py"
app = cosalette.App(
    "myapp",
    exit_after_stale=1800,  # exit 5 after 30 min of stale telemetry
    on_task_failure="restart",  # default: exit 4 once the restart budget is spent
)
```

- `exit_after_stale` shuts the app down cleanly with exit code `5` once a
  telemetry entity or tracked stream has been stale for that many seconds.
- `on_task_failure="restart"` (the default) restarts a framework-started task
  that dies (device, telemetry, periodic or stream handler) with backoff and
  exits with code `4` once `task_max_restarts` is spent; `"exit"` exits with
  `4` on the first failure.
- `COSALETTE_LOOP_STALL_TIMEOUT` turns on the loop-stall watchdog
  ([ADR-088](../adr/ADR-088-opt-in-event-loop-stall-watchdog-with-exit-code-6.md)).
  It ends the process with exit code `6` once the event loop has not run for
  that many seconds; see [Loop-stall watchdog](#loop-stall-watchdog).

See [exit codes](../reference/cli.md#exit-codes).

Docker waits before each restart: 100 ms, doubling up to a cap of one minute, and
back to 100 ms once the container has run for 10 seconds. A crash loop, such as a
bad configuration that exits `1` at once, therefore restarts about once a minute
and `{prefix}/status` flaps between `"offline"` and the heartbeat each time. Every
restart also republishes availability, so Home Assistant entities show
*unavailable* for a few seconds.

!!! warning "Give `restart_on_stale` time before `exit_after_stale` fires"

    With both options set, `exit_after_stale` must outlast the in-place
    recovery, or the app exits before `restart_on_stale` can fix anything.
    See [Using both](../concepts/staleness.md#using-both) for the timing rule.

#### Loop-stall watchdog

A blocked event loop, such as a synchronous call that never returns, never
reaches the stale check, so `exit_after_stale` cannot fire. The LWT reports the
app `"offline"` once the broker drops the connection, but nothing restarts it.
The loop-stall watchdog closes that gap. It is **off by default**; set
`COSALETTE_LOOP_STALL_TIMEOUT` to a number of seconds to turn it on.

- A timer on the event loop records a timestamp every `timeout / 4` seconds (at
  most every 5 s), and a background thread checks it. When the loop has not run
  for longer than the timeout, the thread writes a
  `CRITICAL cosalette: event loop stalled ...` line and every thread's stack to
  stderr and exits with code `6` at once. There is no graceful shutdown: the
  broker publishes the LWT `"offline"` and the restart policy starts the app.
- A loop stuck in C code that holds the GIL stops the thread too. A
  `faulthandler` backstop then exits with code `1`, not `6`, after twice the
  timeout. Its stderr starts with faulthandler's `Timeout (H:MM:SS)!` header
  (`Timeout (0:10:00)!` for a 300 s timeout) followed by every thread's stack,
  without the `CRITICAL` line. `restart: on-failure` and `unless-stopped` still
  restart the container on code `1`.
- The watchdog runs from the moment the app's lifespan has started, which is
  also when the health file is first written, until shutdown begins. Adapter
  entry, `on_configure` hooks, `@app.state` factories and the lifespan may block
  as long as they need; a hang there is not detected.
- An invalid value (`0`, a negative number, text) stops the app at startup with
  exit code `1`.

Pick a timeout well above the longest call that legitimately blocks the loop,
such as a slow synchronous adapter read. A few minutes (`300`) suits most apps;
a single blocking call longer than the timeout restarts the app.

!!! note "One `dump_traceback_later` timer per process"
    The backstop uses `faulthandler.dump_traceback_later`, and Python keeps a
    single process-wide timer for it. Each call replaces the previous one, so
    do not combine the watchdog with your own `dump_traceback_later` calls.

### Probe for orchestrators that act on it

Run a health probe only where something acts on the result: a Kubernetes
liveness probe, a Docker Swarm service, which replaces unhealthy tasks, or an
autoheal container next to plain Docker. The app can write its heartbeat to a
local file, which the `cosalette-health` command checks. This needs no extra
packages in the image and does not depend on the broker. The file is **off by
default**. Set `COSALETTE_HEALTH_FILE` to turn it on:

```yaml title="docker-compose.yml (health file probe)"
services:
  myapp:
    # ...
    restart: unless-stopped
    tmpfs:
      - /tmp   # the health file needs a writable path on a read-only root
    environment:
      COSALETTE_HEALTH_FILE: /tmp/myapp-health.json
    healthcheck:
      test: ["CMD", "cosalette-health"]
      interval: 1m
      timeout: 10s
      retries: 3
      start_period: 2m
      start_interval: 10s   # Docker Engine 25+: probe often only while starting
```

The app writes the file when it starts, before it connects to the broker, and
then every `heartbeat_interval` (every 60 s when heartbeats are disabled). Each
write replaces the file atomically. The file holds the
[heartbeat payload](../reference/payloads.md) plus `written_at` and `interval`.
The app deletes the file on a clean shutdown. The
[Health File](../reference/health-file.md) reference describes the format.

`cosalette-health` reads the path from the same variable and exits `1` when:

- the file is missing or unreadable;
- the file is older than `--max-age`, which defaults to three write intervals.
  This catches a hung or dead app;
- a device has a status listed in `--fail-on`, which defaults to `stale`. Add
  `--fail-on error` to fail on any device in `error` too. Leave that off if
  transient read errors should not mark the container unhealthy.

If the app cannot write the file, for example because the directory is
read-only, it logs one WARNING and keeps running, and the probe reports the
container as unhealthy.

`cosalette-health` is installed with `cosalette`. Use it rather than
`myapp health` or `cosalette health`, which take the same options but start a
Python interpreter and import the framework on every run
([ADR-087](../adr/ADR-087-native-cosalette-health-probe-binary-shipped-in-platform-wheels.md)).
Cost per probe on a 6-core x86_64 host (median of 50 runs):

| Probe | Wall time | CPU time | Peak memory |
|-------|-----------|----------|-------------|
| `cosalette-health`, native binary | 1.2 ms | under 1 ms | 2.2 MiB |
| `cosalette-health`, Python fallback | 60 ms | 59 ms | 15 MiB |
| `cosalette health` | 393 ms | 393 ms | 46 MiB |

On a Raspberry Pi 4 limited to `cpus: 0.5`, one adopter measured 11–13 s per
`myapp health` run, about 100 times the app's idle CPU.

!!! note "Native binary or Python fallback"

    The platform wheels for Linux (glibc and musl x86_64, aarch64, armv7),
    macOS and Windows x86_64 contain the native binary
    ([Supported platforms](../getting-started/index.md#supported-platforms)). On other
    platforms `cosalette-health` is a Python script with the same options and
    exit codes. If pip must build the package from its source distribution, the
    existing maturin/PyO3 build for `cosalette-filters-rs` requires Rust; the
    health fallback adds no additional Rust requirement. To keep the fallback
    cheap:

    - Compile bytecode when you build the image, for example with
      `ENV UV_COMPILE_BYTECODE=1` before `uv sync` in the builder stage. On a
      read-only root filesystem Python cannot cache `.pyc` files, so without
      this every probe compiles its imports again. This also shortens the
      app's own start-up.
    - Probe less often. A long `interval` with `start_period` and
      `start_interval` checks quickly while the app starts and rarely
      afterwards.
    - Size `timeout` for the slowest host, or a slow probe counts as a failure.

#### Checks to avoid

If you need a probe, use the health file probe above. Two checks that look like
alternatives report healthy even when the app is not:

- **Subscribing to `{prefix}/status`**, for example with `mosquitto_sub -C 1`.
  The heartbeat and the LWT are both retained. Once a retained status message
  exists, the broker hands the subscriber the last message at once, even when
  that message is `"offline"`, and the command exits `0`. Before the first
  retained status publish, it waits for a message and can time out even while
  the broker is up. Receiving a message alone does not establish app health.
- **Checking that the process exists**, for example with `pgrep`. A hung app is
  still a running process.

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
