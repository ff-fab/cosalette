#!/bin/sh
# Stable socket paths let VS Code processes share the remapped user's daemon.
# The parent is a fresh root-owned tmpfs; only this user's subdirectory is private.
prepare_rootless_runtime() {
    : "${XDG_RUNTIME_DIR:=/run/user/vscode}"
    : "${DOCKER_HOST:=unix://${XDG_RUNTIME_DIR}/docker.sock}"
    export XDG_RUNTIME_DIR DOCKER_HOST
    runtime_uid="$(id -u)"
    runtime_gid="$(id -g)"
    sudo install -d -m 0700 -o "$runtime_uid" -g "$runtime_gid" "$XDG_RUNTIME_DIR"
    # Named-volume copy-up retains image ownership even when VS Code remaps UID.
    sudo chown "$runtime_uid:$runtime_gid" "$HOME/.local/share/docker"
}
