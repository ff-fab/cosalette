#!/usr/bin/env bash
# Isolated tests for credential-helper cleanup. No Docker daemon is needed.
set -euo pipefail

project_root="$(cd "$(dirname "$0")/../.." && pwd)"
cleanup_script="$project_root/.devcontainer/clean-docker-credential-helpers.sh"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

new_case() {
    case_dir="$test_root/$1"
    mkdir -p "$case_dir/home/.docker" "$case_dir/bin"
    config="$case_dir/home/.docker/config.json"
}

make_helper() {
    cat > "$case_dir/bin/docker-credential-$1" <<HELPER
#!/usr/bin/env bash
$2
HELPER
    chmod +x "$case_dir/bin/docker-credential-$1"
}

run_cleanup() {
    timeout 15s env HOME="$case_dir/home" PATH="$case_dir/bin:$PATH" \
        bash "$cleanup_script" >/dev/null 2>&1
}

assert_json() {
    if ! jq -e "$1" "$config" >/dev/null; then
        echo "FAIL: $2" >&2
        cat "$config" >&2
        exit 1
    fi
}

new_case missing
cat > "$config" <<'JSON'
{"credsStore":"missing","auths":{"docker.io":{"auth":"keep"}}}
JSON
run_cleanup
assert_json 'has("credsStore") | not' "missing helper reference was retained"
assert_json '.auths["docker.io"].auth == "keep"' "auths changed for missing helper"

new_case failing
make_helper bad 'exit 1'
cat > "$config" <<'JSON'
{"credHelpers":{"ghcr.io":"bad"},"auths":{"ghcr.io":{"auth":"keep"}}}
JSON
run_cleanup
assert_json 'has("credHelpers") | not' "failing helper reference was retained"
assert_json '.auths["ghcr.io"].auth == "keep"' "auths changed for failing helper"

new_case working
make_helper good 'printf "{}\n"'
cat > "$config" <<'JSON'
{"credsStore":"good","credHelpers":{"ghcr.io":"good"},"auths":{"ghcr.io":{"auth":"keep"}}}
JSON
cp "$config" "$case_dir/original.json"
run_cleanup
if ! cmp -s "$config" "$case_dir/original.json"; then
    echo "FAIL: working helper config changed" >&2
    exit 1
fi

new_case mixed
make_helper good 'printf "{}\n"'
make_helper bad 'exit 1'
cat > "$config" <<'JSON'
{"credsStore":"bad","credHelpers":{"ghcr.io":"good","docker.io":"bad","example.com":"missing"},"auths":{"ghcr.io":{"auth":"ghcr"},"docker.io":{"auth":"docker"}}}
JSON
run_cleanup
assert_json 'has("credsStore") | not' "mixed config retained bad credsStore"
assert_json '.credHelpers == {"ghcr.io":"good"}' "mixed config did not retain only the working helper"
assert_json '.auths == {"ghcr.io":{"auth":"ghcr"},"docker.io":{"auth":"docker"}}' "mixed config changed auths"

new_case hung
make_helper hung 'exec sleep 30'
make_helper good 'printf "{}\n"'
cat > "$config" <<'JSON'
{"credHelpers":{"ghcr.io":"good","docker.io":"hung"},"auths":{"docker.io":{"auth":"keep"}}}
JSON
run_cleanup
assert_json '.credHelpers == {"ghcr.io":"good"}' "timed-out helper reference was retained"
assert_json '.auths["docker.io"].auth == "keep"' "auths changed for timed-out helper"

echo "Credential helper cleanup tests passed"
