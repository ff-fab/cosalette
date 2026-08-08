---
icon: material/shield-lock
---

# Harden Your Deployment

Security hardening, production logging, and runtime constraints for containerised
cosalette applications.

!!! info "Prerequisites"

    A working [containerized application](containerize.md) and a running
    [Compose deployment](deployment.md).

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

## Docker Hardening

The reference Dockerfile in [Containerize Your Application](containerize.md) includes
baseline hardening: non-root user, minimal runtime image, no shell entrypoint,
immutable venv. For production IoT deployments, consider these additional measures.

### Image scanning

Scan built images for vulnerabilities before deploying them:

```bash
# Scan with Trivy (local)
docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock \
    aquasec/trivy:0.59.2 \
    image --severity HIGH,CRITICAL \
    myapp:latest

# Scan with Grype (alternative)
grype myapp:latest
```

**CI integration:** The cosalette devcontainer image is scanned weekly with Trivy.
Adapt `.github/workflows/devcontainer-build.yml` for your own application images.

### Runtime security

- **Read-only root filesystem:** Add `read_only: true` to the Compose service. If the
  app writes to `/app/data`, mount it as a writable volume.
- **Drop capabilities:** Add `cap_drop: [ALL]` to strip Linux capabilities unless your
  app genuinely needs raw sockets, privileged ports, or device access.
- **No new privileges:** Add `security_opt: ["no-new-privileges:true"]`.
- **User namespace remapping:** Enable Docker's `userns-remap` so container root (UID 0)
  maps to an unprivileged UID on the host. The reference Dockerfile already runs as
  UID 1000, so this is defense-in-depth if a container escape occurs.

Example hardened Compose service:

```yaml title="docker-compose.yml (hardened)"
services:
  myapp:
    image: myapp:latest
    restart: unless-stopped
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    volumes:
      - app-data:/app/data  # writable volume for persistence
    tmpfs:
      - /tmp                # ephemeral tmpfs for scratch space
    environment:
      # ... (same as before)
```

!!! warning "Device access and capabilities"

    If your app binds raw sockets or accesses hardware, you may need to selectively
    add back capabilities like `CAP_NET_RAW` or `CAP_SYS_ADMIN`. Test thoroughly —
    hardening that breaks functionality is worse than no hardening.

### Pinning base images

The reference Dockerfile uses mutable tags (`python:3.14-slim`) for developer
convenience. For production, pin to a full image digest:

```dockerfile
FROM python:3.14-slim@sha256:abc123...
```

Update the digest when Dependabot or Renovate opens a PR for a new base image. This
prevents supply-chain attacks where an attacker compromises a mutable tag.

### Network isolation

Run each app in its own Docker network or limit communication with network policies.
The reference Compose file already keeps the broker and app in a shared network —
external services have no direct access unless you explicitly publish ports.

### Secrets management

Avoid embedding credentials in environment variables or the image. Use Docker secrets
(Swarm) or mount secrets from a secure volume:

```yaml title="docker-compose.yml (secrets)"
services:
  myapp:
    image: myapp:latest
    # ...
    environment:
      MYAPP_MQTT__PASSWORD_FILE: /run/secrets/mqtt_password
    secrets:
      - mqtt_password

secrets:
  mqtt_password:
    file: ./secrets/mqtt_password.txt
```

Then update your app's `Settings` to load passwords from files when `*_FILE` env vars
are set. cosalette does not provide built-in `_FILE` support — implement it in your
app's `Settings.__init__` or use a wrapper like [pydantic-vault](https://github.com/psykzz/pydantic-vault).

A minimal secure implementation:

```python
import os
import stat
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    mqtt_password: str = ""

    def model_post_init(self, __context: object) -> None:
        if path_str := os.environ.get("MYAPP_MQTT__PASSWORD_FILE"):
            path = Path(path_str)
            # Verify restrictive permissions (owner-read-only)
            mode = path.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                raise RuntimeError(f"Secret file {path} is world/group readable")
            self.mqtt_password = path.read_text().strip()
```

!!! warning "Secret file hygiene"

    Always strip whitespace from file contents. Verify file permissions are `0600` or
    stricter (`chmod 600 ./secrets/mqtt_password.txt`). Avoid logging secret values.

---

**Related guides:**

- [Containerize Your Application](containerize.md) — Dockerfile and multi-arch builds
- [Deploy with Docker Compose](deployment.md) — Compose configuration, health checks, persistence, and Ansible rollout
- [Troubleshoot a Deployment](troubleshoot-deployment.md) — diagnosing common problems
