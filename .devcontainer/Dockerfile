# cosalette development container
# Python 3.14 + uv + common dev tools

FROM mcr.microsoft.com/devcontainers/python:3.14

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install additional system dependencies FIRST (ensures curl/bash are available)
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        # General utilities
        jq \
        httpie \
        fzf \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install go-task (task runner - https://taskfile.dev)
RUN sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

# Configure uv to use project-local virtual environments
ENV UV_PROJECT_ENVIRONMENT="/workspace/packages/.venv"

# Install beads (bd) — git-backed issue tracker for AI agents
# https://github.com/steveyegge/beads
# The install script detects platform, downloads the release binary, and places
# it in /usr/local/bin (with a 'beads' alias) automatically.
RUN curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash

# Create .vscode-server directory with proper ownership
# This prevents permission issues when volumes are mounted
RUN mkdir -p /home/vscode/.vscode-server /home/vscode/.vscode-server/extensions \
    && chown -R vscode:vscode /home/vscode/.vscode-server

# Verify installations
RUN uv --version && python --version && task --version && bd version && fzf --version
