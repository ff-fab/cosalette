#!/usr/bin/env bash
# Remove Docker credential helpers whose command is missing or cannot answer.
# Preserve auths and references to working helpers.
set -euo pipefail

docker_config="${HOME}/.docker/config.json"
if [ ! -f "${docker_config}" ] || ! command -v jq >/dev/null 2>&1; then
    exit 0
fi

mapfile -t docker_helpers < <(jq -r \
    '[.credsStore, (.credHelpers // {} | .[])] | map(select(. != null and . != "")) | unique[]' \
    "${docker_config}")
for docker_helper in "${docker_helpers[@]}"; do
    docker_helper_command="docker-credential-${docker_helper}"
    # A present helper can hang on a broken VS Code IPC bridge. Bound each
    # probe so container startup can continue and treat timeouts as failures.
    if ! command -v "${docker_helper_command}" >/dev/null 2>&1 \
        || ! timeout --kill-after=1s 10s "${docker_helper_command}" list >/dev/null 2>&1; then
        docker_config_tmp="$(mktemp "${docker_config}.XXXXXX")"
        chmod 600 "${docker_config_tmp}"
        jq --arg helper "${docker_helper}" \
            'if .credsStore == $helper then del(.credsStore) else . end
             | if .credHelpers then .credHelpers |= with_entries(select(.value != $helper)) else . end
             | if .credHelpers == {} then del(.credHelpers) else . end' \
            "${docker_config}" > "${docker_config_tmp}"
        mv "${docker_config_tmp}" "${docker_config}"
        echo "⚠️ Removed unusable Docker credential helper '${docker_helper}' from config; run docker login for private registries that used it." >&2
    fi
done
