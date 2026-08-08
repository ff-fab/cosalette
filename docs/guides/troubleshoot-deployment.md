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
