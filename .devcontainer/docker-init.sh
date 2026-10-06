#!/bin/sh
# Start a rootless Docker daemon for the development user (cos-2jj7).
#
# Called from post-start.sh as the vscode user when the daemon is not reachable.
# The devcontainer is not privileged: dockerd-rootless.sh (rootlesskit) runs the
# daemon in a user namespace mapped to vscode's subordinate IDs, with slirp4netns
# networking. devcontainer.json supplies the security options this needs and
# sets XDG_RUNTIME_DIR and DOCKER_HOST; see
# .devcontainer/restricted-builder-evaluation.md.
#
# Published ports are bound on the devcontainer's loopback by rootlesskit, so
# clients connect to localhost, never to the bridge gateway.
set -eu

: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export XDG_RUNTIME_DIR
log=/tmp/dockerd-rootless.log

# devcontainer.json mounts the runtime directory as a tmpfs, so no state of a
# previous run is left (the daemon's files there belong to subordinate IDs and
# could not be removed). Fail clearly if it is stale.
if [ -e "${XDG_RUNTIME_DIR}/dockerd-rootless" ]; then
    echo "(*) ${XDG_RUNTIME_DIR} holds state of a previous daemon; it must be a" \
        "tmpfs (see devcontainer.json runArgs)" >&2
    exit 1
fi
mkdir -p "${XDG_RUNTIME_DIR}"

nohup setsid dockerd-rootless.sh >"${log}" 2>&1 </dev/null &

attempt=0
until docker info >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge 60 ]; then
        echo "(*) Rootless dockerd did not become reachable. Daemon log:" >&2
        cat "${log}" >&2 || true
        exit 1
    fi
    sleep 1
done
