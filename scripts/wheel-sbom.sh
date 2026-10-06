#!/usr/bin/env bash
# Generate a CycloneDX SBOM for a cosalette wheel (ADR-017).
#
# Usage: bash scripts/wheel-sbom.sh WHEEL OUTPUT
#
# syft does not look inside a .whl archive, so scanning the file directly
# reports no components. The wheel is unpacked and scanned as a directory:
# syft then reads the dist-info (the cosalette package itself) and the
# dependency list cargo-auditable embeds in the native cosalette-health probe
# of platform wheels (ADR-087). Per-file entries are omitted.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 WHEEL OUTPUT" >&2
  exit 2
fi
wheel="$1"
output="$2"

# Wheel file names: {name}-{version}-{python}-{abi}-{platform}.whl
version="$(basename "$wheel" | cut -d- -f2)"

unpacked="$(mktemp -d)"
trap 'rm -rf "$unpacked"' EXIT
unzip -q "$wheel" -d "$unpacked"

SYFT_FILE_METADATA_SELECTION=none syft "dir:$unpacked" \
  --source-name cosalette --source-version "$version" \
  -o "cyclonedx-json=$output"

if ! grep -q '"pkg:cargo/cosalette-health@' "$output" &&
  [[ -n "$(find "$unpacked" -path '*.data/scripts/cosalette-health*' -type f)" ]]; then
  echo "wheel-sbom: wheel bundles cosalette-health but the SBOM lists no probe crates;" \
    "was it built with cargo-auditable?" >&2
  exit 1
fi
echo "wheel-sbom: wrote $output"
