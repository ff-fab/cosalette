# cosalette development container
# Python 3.14 + uv + common dev tools

FROM mcr.microsoft.com/devcontainers/python:3.14

# Install uv (fast Python package manager)
# renovate: datasource=docker depName=ghcr.io/astral-sh/uv
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /uvx /bin/

# Install syft (SBOM generator — https://github.com/anchore/syft)
# renovate: datasource=docker depName=docker.io/anchore/syft
COPY --from=docker.io/anchore/syft:v1.26.1 /syft /usr/local/bin/syft

# Install system dependencies and Docker CE in a single apt layer.
# Docker CE is baked in here (moved from ghcr.io/devcontainers/features/docker-in-docker:2)
# so it becomes a cacheable BuildKit layer instead of a slow feature bind-mount.
# The daemon startup script (docker-init.sh) is called from post-start.sh.
# renovate: datasource=docker depName=docker versioning=docker
ARG DOCKER_VERSION=29.4.1
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        # General utilities
        jq \
        httpie \
        fzf \
        ripgrep \
        # Docker CE prerequisites
        ca-certificates \
        gnupg \
        lsb-release \
    && install -m 0755 -d /usr/share/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && chmod a+r /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
       https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get -y install --no-install-recommends \
        docker-ce="${DOCKER_VERSION}-1~debian.$(. /etc/os-release && echo "$VERSION_ID")~$(. /etc/os-release && echo "$VERSION_CODENAME")" \
        docker-ce-cli="${DOCKER_VERSION}-1~debian.$(. /etc/os-release && echo "$VERSION_ID")~$(. /etc/os-release && echo "$VERSION_CODENAME")" \
        containerd.io \
        docker-buildx-plugin \
    && groupadd -f docker \
    && usermod -aG docker vscode \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the dockerd startup script (adapted from the docker-in-docker feature).
# Called from post-start.sh on every container start instead of being wired as
# a container entrypoint — simpler and works identically in VS Code, Codespaces,
# and CI (devcontainers/ci).
COPY docker-init.sh /usr/local/share/docker-init.sh
RUN chmod +x /usr/local/share/docker-init.sh

# Install go-task (task runner - https://github.com/go-task/task)
# renovate: datasource=github-releases depName=go-task/task
ARG TASK_VERSION=v3.49.1
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
       amd64) TASK_ARCH='amd64' ;; \
       arm64) TASK_ARCH='arm64' ;; \
       *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    TASK_URL="https://github.com/go-task/task/releases/download/${TASK_VERSION}/task_linux_${TASK_ARCH}.tar.gz"; \
    for i in 1 2 3; do \
        curl -fsSL "${TASK_URL}" -o /tmp/task.tar.gz && break || sleep 5; \
    done; \
    cd /tmp && tar -xzf task.tar.gz task && \
    mv task /usr/local/bin/ && \
    chmod +x /usr/local/bin/task && \
    rm -f /tmp/task.tar.gz

# Install dolt (versioned SQL database backend for the beads/bd issue tracker).
# Baked into the image so the container starts reliably without a runtime download.
# bd on Linux requires an external dolt binary; the embedded dolt in the bd binary
# is macOS-only as of beads v1.0.0.
# renovate: datasource=github-releases depName=dolthub/dolt
ARG DOLT_VERSION=v1.86.2
# SHA-256 digests for supply-chain integrity verification (update on version bump)
ARG DOLT_SHA256_AMD64=478f72a0916580b62945d9249c6201672328d02dc7fe99a6ad54b4a3792031f3
ARG DOLT_SHA256_ARM64=255fcca079a06e85d21332d90379c947b9a714dd881fd61df1eba511bb4f8207
RUN set -eux; \
    ARCH=$(dpkg --print-architecture); \
    case "${ARCH}" in \
      amd64) EXPECTED_SHA="${DOLT_SHA256_AMD64}" ;; \
      arm64) EXPECTED_SHA="${DOLT_SHA256_ARM64}" ;; \
      *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    TARBALL="/tmp/dolt-linux-${ARCH}.tar.gz"; \
    curl -fsSL \
      "https://github.com/dolthub/dolt/releases/download/${DOLT_VERSION}/dolt-linux-${ARCH}.tar.gz" \
      -o "${TARBALL}"; \
    echo "${EXPECTED_SHA}  ${TARBALL}" | sha256sum -c -; \
    tar -xzf "${TARBALL}" -C /tmp; \
    install "/tmp/dolt-linux-${ARCH}/bin/dolt" /usr/local/bin/; \
    rm -rf "/tmp/dolt-linux-${ARCH}" "${TARBALL}"; \
    dolt version

# Install beads (bd) — git-backed issue tracker for AI agents.
# Baked into the image so the VS Code extension (planet57.vscode-beads) can
# discover the project during activation, before any lifecycle scripts run.
# The tarball ships a single static-ish binary; an ICU compatibility symlink
# is created when the binary links against a different ICU than the OS provides.
# renovate: datasource=github-releases depName=gastownhall/beads
ARG BD_VERSION=v1.0.2
# SHA-256 digests for supply-chain integrity verification (update on version bump)
ARG BD_SHA256_AMD64=66280bca14581218684027fee000810c2e40a9c6fe00e876ff64bdcb01a036c0
ARG BD_SHA256_ARM64=f7b60c2398305bdb18d4ece1c84b87951e5bbcd6966fa0d607ac16f9d6e11a4d
RUN set -eux; \
    ARCH=$(dpkg --print-architecture); \
    case "${ARCH}" in \
      amd64) EXPECTED_SHA="${BD_SHA256_AMD64}" ;; \
      arm64) EXPECTED_SHA="${BD_SHA256_ARM64}" ;; \
      *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    VER_NO_V="${BD_VERSION#v}"; \
    TARBALL="/tmp/beads_${VER_NO_V}_linux_${ARCH}.tar.gz"; \
    curl -fsSL \
      "https://github.com/gastownhall/beads/releases/download/${BD_VERSION}/beads_${VER_NO_V}_linux_${ARCH}.tar.gz" \
      -o "${TARBALL}"; \
    echo "${EXPECTED_SHA}  ${TARBALL}" | sha256sum -c -; \
    tar -xzf "${TARBALL}" -C /tmp; \
    install -m 755 /tmp/bd /usr/local/bin/bd; \
    rm -f /tmp/bd "${TARBALL}"; \
    # ICU compatibility shim: the prebuilt bd binary may link against a
    # different ICU major version than the base image provides. Create
    # version-specific symlinks so the dynamic loader resolves them.
    # CAUTION: this spoofs SONAMEs — acceptable here because bd is the
    # only consumer, but do not apply this pattern system-wide. \
    if ! bd version >/dev/null 2>&1; then \
      MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)"; \
      LOCAL_ICU=$(ldconfig -p | grep -oP 'libicui18n\.so\.\K[0-9]+' | head -1); \
      NEEDED_ICU=$(ldd /usr/local/bin/bd 2>/dev/null | grep -oP 'libicui18n\.so\.\K[0-9]+' || true); \
      if [ -n "$LOCAL_ICU" ] && [ -n "$NEEDED_ICU" ] && [ "$LOCAL_ICU" != "$NEEDED_ICU" ]; then \
        for lib in libicui18n libicuuc libicudata; do \
          ln -sf "/lib/${MULTIARCH}/${lib}.so.${LOCAL_ICU}" \
                 "/lib/${MULTIARCH}/${lib}.so.${NEEDED_ICU}"; \
        done; \
        ldconfig; \
      fi; \
    fi; \
    bd version

# Install Rust via rustup-init binary (moved from devcontainer feature to enable BuildKit layer
# caching). Uses per-arch rustup-init binary with sha256 sidecar verification — consistent with
# the dolt/beads pattern elsewhere in this file. RUST_VERSION uses major.minor because rustup
# resolves the latest stable patch automatically (e.g. 1.85 → 1.85.1).
# renovate: datasource=github-releases depName=rust-lang/rust
ARG RUST_VERSION=1.85
# Install to the vscode user's home so devcontainer feature layers (run as root
# after this layer) cannot create root-owned files inside CARGO_HOME.
ENV RUSTUP_HOME=/home/vscode/.rustup \
    CARGO_HOME=/home/vscode/.cargo \
    PATH=/home/vscode/.cargo/bin:$PATH
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
      amd64) RUSTUP_ARCH='x86_64-unknown-linux-gnu' ;; \
      arm64) RUSTUP_ARCH='aarch64-unknown-linux-gnu' ;; \
      *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    RUSTUP_URL="https://static.rust-lang.org/rustup/dist/${RUSTUP_ARCH}/rustup-init"; \
    for i in 1 2 3; do \
        curl -fsSL "${RUSTUP_URL}"        -o /tmp/rustup-init && \
        curl -fsSL "${RUSTUP_URL}.sha256" -o /tmp/rustup-init.sha256 && break || sleep 5; \
    done; \
    EXPECTED_SHA="$(awk '{print $1}' /tmp/rustup-init.sha256)"; \
    echo "${EXPECTED_SHA}  /tmp/rustup-init" | sha256sum -c -; \
    chmod +x /tmp/rustup-init; \
    /tmp/rustup-init -y \
        --default-toolchain "${RUST_VERSION}" \
        --profile minimal \
        --no-modify-path; \
    rustup component add rustfmt clippy; \
    # Verify before chown so any initialisation files are captured in the same chown sweep
    rustc --version; \
    cargo --version; \
    rm -f /tmp/rustup-init /tmp/rustup-init.sha256; \
    chown -R vscode:vscode /home/vscode/.cargo /home/vscode/.rustup

# Configure uv to use project-local virtual environments
ENV UV_PROJECT_ENVIRONMENT="/workspace/packages/.venv"

# Create .vscode-server directory with proper ownership
# This prevents permission issues when volumes are mounted
RUN mkdir -p /home/vscode/.vscode-server /home/vscode/.vscode-server/extensions \
    && chown -R vscode:vscode /home/vscode/.vscode-server

# Verify installations
RUN uv --version && python --version && task --version && fzf --version && rg --version && syft --version
