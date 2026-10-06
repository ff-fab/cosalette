#!/usr/bin/env bash
# Test native crate presence and embedded SBOM handling before publication.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/bin" "$scratch/wheel/cosalette-1.0.0.data/scripts" \
  "$scratch/wheel/cosalette" "$scratch/wheel/cosalette-1.0.0.dist-info/sboms"
cat > "$scratch/bin/syft" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  if [[ "$arg" == dir:* ]]; then
    test -f "${arg#dir:}/cosalette-1.0.0.dist-info/sboms/rust.cdx.json"
    test ! -f "${arg#dir:}/cosalette-1.0.0.dist-info/sboms/rust.cyclonedx.json"
  fi
  if [[ "$arg" == cyclonedx-json=* ]]; then
    printf '%s\n' "$TEST_SBOM" > "${arg#cyclonedx-json=}"
  fi
done
EOF
chmod +x "$scratch/bin/syft"
export PATH="$scratch/bin:$PATH"
printf 'native probe\n' > "$scratch/wheel/cosalette-1.0.0.data/scripts/cosalette-health"
printf 'native extension\n' > "$scratch/wheel/cosalette/_filters_rs.abi3.so"
printf '{}\n' > "$scratch/wheel/cosalette-1.0.0.dist-info/sboms/rust.cyclonedx.json"
wheel="$scratch/cosalette-1.0.0-cp314-abi3-linux_x86_64.whl"
(cd "$scratch/wheel" && zip -qr "$wheel" .)
export TEST_SBOM='{"components":[{"purl":"pkg:cargo/cosalette-health@1.0.0"},{"purl":"pkg:cargo/pyo3@0.29.2"}]}'
bash "$repo_root/scripts/wheel-sbom.sh" "$wheel" "$scratch/sbom.json"
export TEST_SBOM='{"components":[{"purl":"pkg:cargo/cosalette-health@1.0.0"}]}'
if bash "$repo_root/scripts/wheel-sbom.sh" "$wheel" "$scratch/sbom.json" > "$scratch/error.log" 2>&1; then
  echo 'wheel-sbom accepted a native extension without its crates' >&2
  exit 1
fi
grep -q 'extension crates' "$scratch/error.log"
export TEST_SBOM='{"components":[]}'
if bash "$repo_root/scripts/wheel-sbom.sh" "$wheel" "$scratch/sbom.json" > "$scratch/error.log" 2>&1; then
  echo 'wheel-sbom accepted a native probe without its crates' >&2
  exit 1
fi
grep -q 'lists no probe crates' "$scratch/error.log"
rm "$wheel"
rm "$scratch/wheel/cosalette-1.0.0.data/scripts/cosalette-health"
rm "$scratch/wheel/cosalette/_filters_rs.abi3.so"
printf 'METADATA\n' > "$scratch/wheel/METADATA"
(cd "$scratch/wheel" && zip -qr "$wheel" .)
bash "$repo_root/scripts/wheel-sbom.sh" "$wheel" "$scratch/sbom.json"
echo 'Wheel SBOM policy tests passed'
