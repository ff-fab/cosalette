#!/usr/bin/env bash
# Test probe presence detection independently of the release workflow.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/bin" "$scratch/wheel/cosalette-1.0.0.data/scripts"
cat > "$scratch/bin/syft" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  if [[ "$arg" == cyclonedx-json=* ]]; then
    printf '%s\n' "$TEST_SBOM" > "${arg#cyclonedx-json=}"
  fi
done
EOF
chmod +x "$scratch/bin/syft"
export PATH="$scratch/bin:$PATH"
printf 'native probe\n' > "$scratch/wheel/cosalette-1.0.0.data/scripts/cosalette-health"
wheel="$scratch/cosalette-1.0.0-cp314-abi3-linux_x86_64.whl"
(cd "$scratch/wheel" && zip -qr "$wheel" .)
export TEST_SBOM='{"components":[{"purl":"pkg:cargo/cosalette-health@1.0.0"}]}'
bash "$repo_root/scripts/wheel-sbom.sh" "$wheel" "$scratch/sbom.json"
export TEST_SBOM='{"components":[]}'
if bash "$repo_root/scripts/wheel-sbom.sh" "$wheel" "$scratch/sbom.json" > "$scratch/error.log" 2>&1; then
  echo 'wheel-sbom accepted a native probe without its crates' >&2
  exit 1
fi
grep -q 'lists no probe crates' "$scratch/error.log"
rm "$wheel"
rm "$scratch/wheel/cosalette-1.0.0.data/scripts/cosalette-health"
printf 'METADATA\n' > "$scratch/wheel/METADATA"
(cd "$scratch/wheel" && zip -qr "$wheel" .)
bash "$repo_root/scripts/wheel-sbom.sh" "$wheel" "$scratch/sbom.json"
echo 'Wheel SBOM policy tests passed'
