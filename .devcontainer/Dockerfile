# cosalette development container
# Python 3.14 + uv + common dev tools

FROM mcr.microsoft.com/devcontainers/python:3.14

# Install uv (fast Python package manager)
# renovate: datasource=docker depName=ghcr.io/astral-sh/uv
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/


# Install syft (SBOM generator — https://github.com/anchore/syft)
# renovate: datasource=docker depName=docker.io/anchore/syft
COPY --from=docker.io/anchore/syft:v1.42.3 /syft /usr/local/bin/syft


# Install additional system dependencies
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        jq \
        httpie \
        fzf \
        ripgrep \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install go-task (task runner — https://taskfile.dev)
# renovate: datasource=github-releases depName=go-task/task
ARG TASK_VERSION=v3.50.0
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
        amd64) TASK_ARCH='amd64' ;; \
        arm64) TASK_ARCH='arm64' ;; \
        *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    TASK_TARBALL="task_linux_${TASK_ARCH}.tar.gz"; \
    TASK_URL="https://github.com/go-task/task/releases/download/${TASK_VERSION}/${TASK_TARBALL}"; \
    TASK_CHECKSUMS_URL="https://github.com/go-task/task/releases/download/${TASK_VERSION}/task_checksums.txt"; \
    for i in 1 2 3; do \
        curl -fsSL "${TASK_URL}" -o /tmp/task.tar.gz && break || sleep 5; \
    done; \
    for i in 1 2 3; do \
        curl -fsSL "${TASK_CHECKSUMS_URL}" -o /tmp/task_checksums.txt && break || sleep 5; \
    done; \
    EXPECTED_SHA=$(grep "  ${TASK_TARBALL}$" /tmp/task_checksums.txt | awk '{print $1}'); \
    echo "${EXPECTED_SHA}  /tmp/task.tar.gz" | sha256sum -c -; \
    cd /tmp && tar -xzf task.tar.gz task && \
    mv task /usr/local/bin/ && \
    chmod +x /usr/local/bin/task && \
    rm -f /tmp/task.tar.gz /tmp/task_checksums.txt

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
    # ICU compatibility shim: the prebuilt bd binary may link against a \
    # different ICU major version than the base image provides. Create \
    # version-specific symlinks so the dynamic loader resolves them. \
    # CAUTION: this spoofs SONAMEs — acceptable here because bd is the \
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

# Install opencode (AI coding agent — https://opencode.ai)
# renovate: datasource=github-releases depName=anomalyco/opencode
ARG OPENCODE_VERSION=v1.14.25
# SHA-256 digests for supply-chain integrity verification (update on version bump)
ARG OPENCODE_SHA256_AMD64=6542db973fbf404707d275d824fcd7eef20a8003788114eb85067402daf730db
ARG OPENCODE_SHA256_ARM64=0cee21589d4936619bcd6131342121c0d20ef245fbec314009c89b3f9338348d
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

# Configure uv to use project-local virtual environments
ENV UV_PROJECT_ENVIRONMENT="/workspaces/cosalette/.venv"

# Create .vscode-server directory with proper ownership
# This prevents permission issues when volumes are mounted
RUN mkdir -p /home/vscode/.vscode-server /home/vscode/.vscode-server/extensions \
    && chown -R vscode:vscode /home/vscode/.vscode-server

# Verify installations
RUN uv --version && python --version && task --version && fzf --version && dolt version && bd version && opencode --version && syft --version
