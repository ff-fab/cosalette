# cosalette development container
# Python 3.14 + uv + common dev tools

# renovate: datasource=docker depName=mcr.microsoft.com/devcontainers/python versioning=docker
FROM mcr.microsoft.com/devcontainers/python:3.14@sha256:3b3c45535947751e9dfcd513ef5bee858d221844f55e278e92d1cd8f5b1f2972

# Set shell options for robust RUN commands
# -e: exit on error
# -u: error on undefined variables
# -o pipefail: propagate pipe failures (hadolint DL4006)
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

# Install uv (fast Python package manager)
# renovate: datasource=docker depName=ghcr.io/astral-sh/uv
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /uvx /bin/

# Install syft (SBOM generator — https://github.com/anchore/syft)
# renovate: datasource=docker depName=docker.io/anchore/syft
COPY --from=docker.io/anchore/syft:v1.52.0@sha256:500e2d872ac019436926e8322b4fc1f39441d94d21f6f4046c6ff29b30e8cb02 /syft /usr/local/bin/syft

# Install system dependencies and Docker CE in a single apt layer.
# Docker CE is baked in here (moved from ghcr.io/devcontainers/features/docker-in-docker:2)
# so it becomes a cacheable BuildKit layer instead of a slow feature bind-mount.
# The daemon startup script (docker-init.sh) is called from post-start.sh.
# renovate: datasource=docker depName=docker versioning=docker
ARG DOCKER_VERSION=29.8.1
# renovate: datasource=github-releases depName=containerd/containerd extractVersion=^v(?<version>.*)$
ARG CONTAINERD_VERSION=2.3.6
# renovate: datasource=github-releases depName=docker/buildx extractVersion=^v(?<version>.*)$
ARG BUILDX_VERSION=0.37.1
# DL3008 suppressed for general utility packages (jq, httpie, fzf, ripgrep).
# These are intentionally unpinned: they come from Debian's signed repos with
# built-in integrity checks, security patches arrive via base image updates,
# and version pins would make maintenance across Debian releases impractical.
# Critical infrastructure (Docker, containerd, buildx above) IS pinned via ARG.
# SC1091: shellcheck can't follow /etc/os-release in static analysis - benign.
# DL3008: general utilities intentionally unpinned (see comment above).
# Docker official GPG key fingerprint — verify at https://docs.docker.com/engine/install/debian/
# hadolint ignore=DL3008,SC1091
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
    && curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 30 \
       https://download.docker.com/linux/debian/gpg -o /tmp/docker.gpg.asc \
       || { echo "Docker GPG key download failed" >&2; exit 1; } \
    && DOCKER_GPG_FP="$(gpg --show-keys --with-colons /tmp/docker.gpg.asc \
       | awk -F: '/^fpr:/{print $10; exit}')" \
    && [ "${DOCKER_GPG_FP}" = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88" ] \
       || { echo "Docker GPG key fingerprint mismatch: ${DOCKER_GPG_FP}" >&2; exit 1; } \
    && gpg --dearmor < /tmp/docker.gpg.asc > /usr/share/keyrings/docker-archive-keyring.gpg \
    && chmod a+r /usr/share/keyrings/docker-archive-keyring.gpg \
    && rm /tmp/docker.gpg.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
       https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get -y install --no-install-recommends \
        "docker-ce=5:${DOCKER_VERSION}-1~debian.$(. /etc/os-release && echo "$VERSION_ID")~$(. /etc/os-release && echo "$VERSION_CODENAME")" \
        "docker-ce-cli=5:${DOCKER_VERSION}-1~debian.$(. /etc/os-release && echo "$VERSION_ID")~$(. /etc/os-release && echo "$VERSION_CODENAME")" \
        "containerd.io=${CONTAINERD_VERSION}-1~debian.$(. /etc/os-release && echo "$VERSION_ID")~$(. /etc/os-release && echo "$VERSION_CODENAME")" \
        "docker-buildx-plugin=${BUILDX_VERSION}-1~debian.$(. /etc/os-release && echo "$VERSION_ID")~$(. /etc/os-release && echo "$VERSION_CODENAME")" \
    && groupadd -f docker \
    && usermod -aG docker vscode \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the dockerd startup script (adapted from the docker-in-docker feature).
# Called from post-start.sh on every container start instead of being wired as
# a container entrypoint — simpler and works identically in VS Code, Codespaces,
# and CI (devcontainers/ci).
COPY --chmod=0755 docker-init.sh /usr/local/share/docker-init.sh

# Install go-task (task runner - https://github.com/go-task/task)
# renovate: datasource=github-releases depName=go-task/task
ARG TASK_VERSION=v3.53.1
# SHA-256 digests for supply-chain integrity verification (update on version bump)
ARG TASK_SHA256_AMD64=a54a408f6861ff921f6e87774180db31bacd8c1e7c944ca696db9fea49a82fc7
ARG TASK_SHA256_ARM64=e3ad19101493a0112e1f22ae8ccc54bf03e533b1076a0ca1e6c782a09ad2e588
# DL3003 suppressed: cd in a single RUN is scoped to that layer; WORKDIR would
# persist across layers. Using cd here is intentional and safe.
# hadolint ignore=DL3003
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
       amd64) TASK_ARCH='amd64'; EXPECTED_SHA="${TASK_SHA256_AMD64}" ;; \
       arm64) TASK_ARCH='arm64'; EXPECTED_SHA="${TASK_SHA256_ARM64}" ;; \
       *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    TASK_URL="https://github.com/go-task/task/releases/download/${TASK_VERSION}/task_linux_${TASK_ARCH}.tar.gz"; \
    DOWNLOAD_SUCCESS=0; \
    for i in 1 2 3 4 5; do \
        echo "Attempting to download go-task (attempt $i/5)..."; \
        rm -f /tmp/task.tar.gz; \
        if curl -fsSL "${TASK_URL}" -o /tmp/task.tar.gz; then \
            echo "Download successful on attempt $i"; \
            DOWNLOAD_SUCCESS=1; \
            break; \
        else \
            echo "Download failed on attempt $i"; \
            sleep 5; \
        fi; \
    done; \
    if [ "$DOWNLOAD_SUCCESS" -eq 0 ]; then \
        echo "ERROR: Failed to download go-task after 5 attempts" >&2; \
        exit 1; \
    fi; \
    echo "${EXPECTED_SHA}  /tmp/task.tar.gz" | sha256sum -c -; \
    cd /tmp && tar -xzf task.tar.gz task && \
    mv task /usr/local/bin/ && \
    chmod +x /usr/local/bin/task && \
    rm -f /tmp/task.tar.gz

# Install dolt (versioned SQL database backend for the beads/bd issue tracker).
# Baked into the image so the container starts reliably without a runtime download.
# bd on Linux requires an external dolt binary; the embedded dolt in the bd binary
# is macOS-only as of beads v1.0.0.
# renovate: datasource=github-releases depName=dolthub/dolt
ARG DOLT_VERSION=v2.3.5
# SHA-256 digests for supply-chain integrity verification (update on version bump)
ARG DOLT_SHA256_AMD64=c49d4c3e004cf1581ba0d4a00c5023a26f84eb2ec15d5fe876eed36d5343f463
ARG DOLT_SHA256_ARM64=9ce70fc81e50139e97758ef7f4dc57e9583e4e5ef05ad75d7535c30caa161387
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
ARG BD_VERSION=v1.3.0
# SHA-256 digests for supply-chain integrity verification (update on version bump)
ARG BD_SHA256_AMD64=2f92b904ecf35b607e44dc5c39229173af69c54f1183e8d709f1773540cdcf3b
ARG BD_SHA256_ARM64=4ce9446a68edc13202b76c84a47d66fb3dad2787b6464c9522a108faf9c1c608
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
# resolves the latest stable patch automatically (e.g. 1.98 → 1.98.1).
# renovate: datasource=github-releases depName=rust-lang/rust
ARG RUST_VERSION=1.98
# renovate: datasource=crate depName=cargo-audit
ARG CARGO_AUDIT_VERSION=0.22.2
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
    for _ in 1 2 3; do \
        if curl -fsSL "${RUSTUP_URL}"        -o /tmp/rustup-init && \
           curl -fsSL "${RUSTUP_URL}.sha256" -o /tmp/rustup-init.sha256; then break; fi; \
        sleep 5; \
    done; \
    EXPECTED_SHA="$(awk '{print $1}' /tmp/rustup-init.sha256)"; \
    echo "${EXPECTED_SHA}  /tmp/rustup-init" | sha256sum -c -; \
    chmod +x /tmp/rustup-init; \
    /tmp/rustup-init -y \
        --default-toolchain "${RUST_VERSION}" \
        --profile minimal \
        --no-modify-path; \
    rustup component add rustfmt clippy; \
    cargo install cargo-audit --version "${CARGO_AUDIT_VERSION}" --locked; \
    # Verify before chown so any initialisation files are captured in the same chown sweep
    rustc --version; \
    cargo --version; \
    cargo audit --version; \
    rm -f /tmp/rustup-init /tmp/rustup-init.sha256; \
    chown -R vscode:vscode /home/vscode/.cargo /home/vscode/.rustup

# Install opencode (AI coding agent — https://opencode.ai)
# renovate: datasource=github-releases depName=anomalyco/opencode
ARG OPENCODE_VERSION=v1.18.32
# SHA-256 digests for supply-chain integrity verification (update on version bump)
ARG OPENCODE_SHA256_AMD64=3046e0404fdc60fb80307e7a47824ba07477364178a4d09baa8548496dd6d43b
ARG OPENCODE_SHA256_ARM64=568461b7d4d8c19865c97e9a1102e613049c6039d01fe772154de873c1865840
RUN set -eux; \
    ARCH=$(dpkg --print-architecture); \
    case "${ARCH}" in \
      amd64) ARCH_NAME='x64';   EXPECTED_SHA="${OPENCODE_SHA256_AMD64}" ;; \
      arm64) ARCH_NAME='arm64'; EXPECTED_SHA="${OPENCODE_SHA256_ARM64}" ;; \
      *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    TARBALL="/tmp/opencode-linux-${ARCH_NAME}.tar.gz"; \
    curl -fsSL \
      "https://github.com/anomalyco/opencode/releases/download/${OPENCODE_VERSION}/opencode-linux-${ARCH_NAME}.tar.gz" \
      -o "${TARBALL}"; \
    echo "${EXPECTED_SHA}  ${TARBALL}" | sha256sum -c -; \
    tar -xzf "${TARBALL}" -C /tmp opencode; \
    install -m 755 /tmp/opencode /usr/local/bin/opencode; \
    rm -f /tmp/opencode "${TARBALL}"; \
    opencode --version

# Install Claude Code CLI (Anthropic — https://claude.com/claude-code)
# The installer downloads the CLI binary and verifies it against a SHA-256
# checksum from Anthropic's release manifest before installing. We pin the CLI
# version (passed through to the installer) so builds are reproducible; bump via
# Renovate. The bootstrap script itself is fetched over TLS from claude.ai and
# is trusted rather than checksum-pinned.
# renovate: datasource=npm depName=@anthropic-ai/claude-code
ARG CLAUDE_CODE_VERSION=2.1.283
# Claude Code is a local/interactive dev tool only — CI never invokes `claude`.
# Default to installing it (local devcontainer builds via VS Code / `devcontainer
# up` get it out of the box); the CI devcontainer-build workflows set
# INSTALL_CLAUDE_CODE=false (via devcontainer.json's build.args + localEnv, see
# .devcontainer/devcontainer.json) so CI image builds skip it entirely, which
# also sidesteps this whole class of PATH bug in CI.
ARG INSTALL_CLAUDE_CODE=true
RUN set -eux; \
    if [ "${INSTALL_CLAUDE_CODE}" = "true" ]; then \
      INSTALLER="/tmp/claude-install.sh"; \
      curl -fsSL https://claude.ai/install.sh -o "${INSTALLER}"; \
      # The installer places the binary at "${HOME}/.local/bin/claude". This
      # layer runs as root, so an unqualified ${HOME} resolves to /root — but
      # devcontainer.json sets remoteUser=vscode and mode-0700 /root is not
      # even readable by that user, so the install (and any symlink pointing
      # at it) is useless at runtime. Override HOME for the installer call so
      # the binary lands in the vscode user's home, then chown, mirroring the
      # install-as-root-then-chown pattern used by the Rust layer above.
      install -d -m 0755 -o vscode -g vscode /home/vscode/.local /home/vscode/.local/bin; \
      HOME=/home/vscode bash "${INSTALLER}" "${CLAUDE_CODE_VERSION}"; \
      rm -f "${INSTALLER}"; \
      # The installer also writes outside ~/.local (~/.claude, ~/.claude.json,
      # ~/.cache/claude/staging). A root-owned ~/.cache makes post-create's
      # `uv sync` die with "Failed to initialize cache at /home/vscode/.cache/uv"
      # (cos-l10x), so hand back everything it left root-owned. Chowning only
      # the strays avoids copying the whole ~/.rustup / ~/.cargo into this layer.
      find /home/vscode \( ! -user vscode -o ! -group vscode \) \
        -exec chown -h vscode:vscode {} +; \
      # The installer only advises adding that dir to PATH via shell rc files
      # (~/.bashrc etc). Non-interactive Docker RUN layers never source those,
      # so `claude` is not found in later RUN steps (or in non-login
      # CI/devcontainer shells). Symlink into /usr/local/bin, already on PATH
      # everywhere, matching how task/dolt/bd/opencode are installed above.
      # The explicit test fails the build loudly if the installer ever puts the
      # binary somewhere else, instead of baking a dangling symlink into the
      # image (which post-create.sh would then silently skip over).
      test -x /home/vscode/.local/bin/claude; \
      ln -sfn /home/vscode/.local/bin/claude /usr/local/bin/claude; \
      claude --version; \
    fi

# Configure uv to use project-local virtual environments
ENV UV_PROJECT_ENVIRONMENT="/workspace/packages/.venv"

# Create .vscode-server directory with proper ownership
# This prevents permission issues when volumes are mounted
RUN mkdir -p /home/vscode/.vscode-server /home/vscode/.vscode-server/extensions \
    && chown -R vscode:vscode /home/vscode/.vscode-server

# Verify installations
RUN set -eux; \
    uv --version && python --version && task --version && fzf --version && rg --version \
      && dolt version && bd version && opencode --version && syft --version; \
    if [ "${INSTALL_CLAUDE_CODE}" = "true" ]; then claude --version; fi

# OCI image labels — placed last so they don't invalidate build cache layers
LABEL org.opencontainers.image.title="cosalette devcontainer" \
      org.opencontainers.image.description="Development container for cosalette — Python 3.14, uv, Rust, Docker, task, dolt, beads, and related tooling" \
      org.opencontainers.image.source="https://github.com/ff-fab/cosalette" \
      org.opencontainers.image.licenses="Apache-2.0"
