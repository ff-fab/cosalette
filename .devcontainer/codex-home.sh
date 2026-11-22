#!/bin/sh
# Docker creates a new named volume as root, so hand CODEX_HOME (mounted in
# devcontainer.json) to the developer with a private mode on every start.
prepare_codex_home() {
    codex_home="${CODEX_HOME:-$HOME/.codex}"
    # install would follow a symlink and run root chown on its target.
    if [ -L "$codex_home" ]; then
        echo "⚠️  $codex_home is a symlink; not changing its ownership" >&2
        return 1
    fi
    sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" "$codex_home"
}
