---
icon: material/bug-outline
---

# Troubleshoot a Deployment

Diagnose and fix common problems with containerised cosalette applications.

## Troubleshooting

**Container starts but no MQTT connection**
:   The broker hostname must be the Compose **service name** (e.g., `mosquitto`),
    not `localhost`. Inside a container, `localhost` refers to the container itself.
    Verify name resolution with `docker exec myapp getent hosts mosquitto`.

**MQTT reconnect loop with `Connection reset by peer` or an `SSL` error**
:   The client is attempting TLS against a broker that has no TLS listener.
    `MYAPP_MQTT__TLS` defaults to `true` since 0.7.0 (ADR-062), so an app
    upgraded across that boundary starts a TLS handshake the broker cannot
    answer. The broker never learns the client ID and logs
    `Client <unknown> disconnected due to protocol error`, while the client
    retries with a growing backoff — which looks like a flaky broker rather
    than a misconfiguration.

    cosalette names this case once per run, before the first successful
    connection:

    ```text
    TLS handshake with mqtt.example:1883 failed ([SSL: UNEXPECTED_EOF_WHILE_READING] ...)
    — is the broker listening in plaintext? MQTT TLS is enabled by default
    (ADR-062); set MQTT__TLS=false if this broker has no TLS listener.
    ```

    Set `MYAPP_MQTT__TLS=false` for a plaintext broker, or point the app at the
    broker's TLS listener (usually port 8883) and set `MYAPP_MQTT__TLS_CA_FILE`.
    The hint is advisory — it never stops the app, and reconnection is unaffected.

**Permission denied on `/dev/ttyUSB0`**
:   The container needs access to the host device. Options:

    1. Add `device_cgroup_rules: ['c 188:* rmw']` under the service — scopes
       access to a single device major/minor (preferred).
    2. Map the specific device: `devices: ['/dev/ttyUSB0:/dev/ttyUSB0']`.
    3. Add the container user to the `dialout` group (`group_add: [dialout]`).

    Do **not** reach for `privileged: true` to fix a device-permission error: it
    grants the container full access to every host device and is a trivial
    container-escape path on a Pi with GPIO/i²c/serial passthrough. The scoped
    options above are sufficient; never use `privileged: true` in production.

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

---

**Related guides:**

- [Containerize Your Application](containerize.md) — Dockerfile and multi-arch builds
- [Deploy with Docker Compose](deployment.md) — Compose configuration, health checks, and persistence
- [Harden Your Deployment](harden.md) — security hardening and production logging
