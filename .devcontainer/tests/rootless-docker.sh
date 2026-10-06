#!/usr/bin/env bash
# Run inside a devcontainer started from devcontainer.json (post-start done).
# Checks that the container is unprivileged and that the rootless Docker daemon
# can build an image and serve a published port on localhost, the path
# testcontainers uses for the MQTT integration tests (cos-2jj7).
set -euo pipefail

image=eclipse-mosquitto:2
tag=cosalette-rootless-check:latest
name="cosalette-rootless-check-$$"

cleanup() {
    docker rm -f "${name}" >/dev/null 2>&1 || true
    docker image rm "${tag}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# A privileged container has the full capability bounding set.
source "$(dirname "${BASH_SOURCE[0]}")/capabilities.sh"
full_mask="$(full_capability_mask "$(cat /proc/sys/kernel/cap_last_cap)")"
bounding="$(awk '/^CapBnd:/ {print $2}' /proc/self/status)"
if [ "${bounding}" = "${full_mask}" ]; then
    echo "devcontainer runs privileged (CapBnd=${bounding})" >&2
    exit 1
fi

if ! docker info --format '{{json .SecurityOptions}}' | grep -q 'name=rootless'; then
    echo "Docker daemon is not rootless" >&2
    docker info >&2 || true
    exit 1
fi

printf 'FROM %s\nRUN echo built > /built\n' "${image}" | docker build --quiet --tag "${tag}" -
docker run --detach --name "${name}" --publish 127.0.0.1::1883 "${tag}" \
    mosquitto -c /mosquitto-no-auth.conf >/dev/null
port="$(docker port "${name}" 1883/tcp | head -1 | cut -d: -f2)"

for _ in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
        echo "rootless Docker: build and published port on localhost:${port} OK"
        exit 0
    fi
    sleep 1
done
echo "published port ${port} not reachable on localhost" >&2
docker logs "${name}" >&2 || true
exit 1
