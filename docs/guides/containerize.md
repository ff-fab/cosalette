---
icon: material/docker
---

# Containerize Your Application

How to package a cosalette application as a Docker image, including hardware-specific
customisation and multi-architecture builds for Raspberry Pi targets.

!!! info "Prerequisites"

    - Docker Engine ≥ 20.10 (with BuildKit)
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
# Use a stable minor-series tag; replace with a fully pinned
# version tag or image digest for strictly reproducible builds.
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /bin/uv

WORKDIR /app

# Copy dependency metadata first — this layer is
# cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev
# extras). --frozen ensures the lock file is used
# as-is without re-resolving.
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the rest of the source tree and install
# the project itself. Make sure to add a .dockerignore
# excluding .git/, tests/, docs/, and *.md to keep
# the build context small.
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
    [Docker Compose — devices](deployment.md#docker-compose) in the Deploy guide).

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

---

**Related guides:**

- [Deploy with Docker Compose](deployment.md) — Compose configuration, health checks, persistence, and Ansible rollout
- [Harden Your Deployment](harden.md) — security hardening, image scanning, and production logging
- [Troubleshoot a Deployment](troubleshoot-deployment.md) — diagnosing common problems
