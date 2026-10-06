#!/usr/bin/env bash
# Technique: Boundary Value Analysis / Error Guessing — host UID remapping,
# private runtime ownership and kernels with different capability counts.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../docker-runtime.sh"
source "$(dirname "${BASH_SOURCE[0]}")/capabilities.sh"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
for developer_uid in 1000 1234; do
    (
        id() {
            case "$1" in
                -u) echo "$developer_uid" ;;
                -g) echo 2345 ;;
                *) return 1 ;;
            esac
        }
        sudo() { printf '%s\n' "$*" >> "$scratch/calls"; }
        export XDG_RUNTIME_DIR="$scratch/runtime"
        unset DOCKER_HOST
        : > "$scratch/calls"
        prepare_rootless_runtime
        [[ "$DOCKER_HOST" == "unix://$XDG_RUNTIME_DIR/docker.sock" ]]
        grep -Fx "install -d -m 0700 -o $developer_uid -g 2345 $scratch/runtime" "$scratch/calls" >/dev/null
        grep -Fx "chown $developer_uid:2345 $HOME/.local/share/docker" "$scratch/calls" >/dev/null
    )
done
[[ "$(full_capability_mask 0)" == 0000000000000001 ]]
[[ "$(full_capability_mask 37)" == 0000003fffffffff ]]
[[ "$(full_capability_mask 40)" == 000001ffffffffff ]]
[[ "$(full_capability_mask 41)" == 000003ffffffffff ]]
[[ "$(full_capability_mask 63)" == ffffffffffffffff ]]
! full_capability_mask 64 >/dev/null 2>&1
! full_capability_mask invalid >/dev/null 2>&1
printf 'rootless runtime: UID ownership, socket paths and capability masks OK\n'
