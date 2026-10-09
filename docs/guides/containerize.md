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

# Compile bytecode at build time. The non-root runtime
# user cannot write .pyc files, so without this every
# start compiles all imports again (see Memory Footprint).
ENV UV_COMPILE_BYTECODE=1

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

## Memory Footprint

An adopter's app with Home Assistant discovery, one BLE sensor and an MQTT client
uses about 52 MiB of resident memory on CPython 3.14. Most of it is loaded
code: Python keeps every imported module in memory, so the footprint drops only when
the app loads fewer modules. The file-backed part (about 18 MiB) is shared between
containers that run the same image.

cosalette keeps its own share small: `app.cli()` starts a plain run without loading
the Typer CLI, and cron schedules, publish strategies, the command and stream
runners and the discovery generator load only when the app uses them.

Two settings make a measurable difference:

- **Compile bytecode in the image.** The Dockerfile above sets
  `UV_COMPILE_BYTECODE=1`. Without it, a non-root user cannot write `.pyc` files and
  Python compiles every import at each start. One adopter measured 4 MiB more
  resident memory, a 7 MiB higher peak and about 70 % more start-up CPU without it.
- **Keep the default allocator.** Do not set `PYTHONMALLOC=malloc`. On a musl
  (Alpine) image it increased resident memory by about 3 MiB. `MALLOC_ARENA_MAX`
  is a glibc allocator control and is ignored by musl. On glibc, even a process
  with two or three threads may use multiple arenas, so its memory and contention
  effects depend on the workload.

## Slim Images

Image size matters on small hosts and slow links, even where it does not change
resident memory. Three things keep a cosalette image small: leave out rich, install
only the extras the app uses, and know which parts of the wheel are there on purpose.

### Leave Out rich

Typer, which cosalette uses for its CLI, requires `rich`, which pulls in `pygments`,
`markdown-it-py` and `mdurl` (about 18 MB). They load only for `--help` and CLI
errors, so they add image size, not resident memory. cosalette cannot drop them from
its own dependencies, but an image can skip them at install time. Add the four
`--no-install-package` flags to **both** `uv sync` lines of the builder stage and set
`TYPER_USE_RICH=0` in the runtime stage:

```dockerfile title="Dockerfile (changed lines)"
# Builder stage
RUN uv sync --frozen --no-dev --no-install-project \
    --no-install-package rich --no-install-package pygments \
    --no-install-package markdown-it-py --no-install-package mdurl

COPY . .
RUN uv sync --frozen --no-dev \
    --no-install-package rich --no-install-package pygments \
    --no-install-package markdown-it-py --no-install-package mdurl

# Runtime stage
ENV TYPER_USE_RICH=0
```

The lockfile is used as-is and nothing is re-resolved, so your dev environment keeps
rich. The recipe works with the `ghcr.io/astral-sh/uv:0.6` image pinned above.

- **Plain output.** Help and usage errors of the CLIs cosalette builds (the app CLI,
  its `schema` group and the `cosalette` command) fall back to plain output when rich
  is missing, with the same exit codes.
- **`TYPER_USE_RICH=0`** is needed only when the app creates its own
  `typer.Typer()`. Typer decides whether to use rich from this variable alone, so
  without it such a CLI crashes on `--help` with `ModuleNotFoundError: No module
  named 'rich'`. The variable is harmless for every other app.
- **`pip check` reports rich as missing.** `uv pip check` and `pip check` in the image
  report that typer requires rich. This is expected.

!!! warning "Do not use `override-dependencies` for this"

    Older cosalette docs recommended
    `[tool.uv] override-dependencies = ["rich; sys_platform == 'never'"]`. Overrides
    apply to the whole lock, including dev dependency groups, so the entry also
    removes rich from dev tools that need it, such as pip-audit, cyclonedx and
    fastmcp. Remove the entry and use the flags above (ADR-005 amendment).

### Install Only the Extras You Use

Most apps need no cosalette extra at runtime. Runtime discovery (`app.discovery()`)
and a schema file in JSON format need none. `cosalette[schema]` is needed only for a
YAML schema file, for publish-time payload validation and for the `schema` CLI
commands, and `cosalette[mcp]` only for the MCP server, which runs in your editor,
not in the image. Check the
[Which Extra Do I Need?](schema-enforcement.md#which-extra-do-i-need) table before
you add an extra to the app's runtime dependencies. If CI runs the `schema` commands,
put the extra in a dev dependency group: `uv sync --no-dev` then keeps it out of the
image.

### What the Runtime Wheel Contains

The cosalette wheel intentionally ships some content that a running daemon does not
use: the AI guidance and help text, the ADR index (gzip-compressed), the `cosalette`
CLI with its `health` and `schema` commands, and the pytest plugin (ADR-034). They
cost about 1 MB, and the `cosalette health` probe must be available on every
install. Leave them in place; the savings above are larger.

---

**Related guides:**

- [Deploy with Docker Compose](deployment.md) — Compose configuration, health checks, persistence, and Ansible rollout
- [Harden Your Deployment](harden.md) — security hardening, image scanning, and production logging
- [Troubleshoot a Deployment](troubleshoot-deployment.md) — diagnosing common problems
