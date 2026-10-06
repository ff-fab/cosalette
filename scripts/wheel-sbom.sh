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
#
# The _filters_rs extension is built by maturin, which has no cargo-auditable
# option but embeds a CycloneDX SBOM of its crates in the dist-info (PEP 770,
# dist-info/sboms/*.cyclonedx.json). syft's sbom-cataloger reads embedded SBOM
# files only under names such as *.cdx.json, so the unpacked copies are renamed
# to match; the SBOM then cites that renamed path as the crates' location.
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

while IFS= read -r -d '' embedded; do
  mv "$embedded" "${embedded%.cyclonedx.json}.cdx.json"
done < <(find "$unpacked" -path '*.dist-info/sboms/*.cyclonedx.json' -type f -print0)

SYFT_FILE_METADATA_SELECTION=none syft "dir:$unpacked" \
  --select-catalogers +sbom-cataloger \
  --source-name cosalette --source-version "$version" \
  -o "cyclonedx-json=$output"

if ! grep -q '"pkg:cargo/cosalette-health@' "$output" &&
  [[ -n "$(find "$unpacked" -path '*.data/scripts/cosalette-health*' -type f)" ]]; then
  echo "wheel-sbom: wheel bundles cosalette-health but the SBOM lists no probe crates;" \
    "was it built with cargo-auditable?" >&2
  exit 1
fi
if ! grep -q '"pkg:cargo/pyo3@' "$output" &&
  [[ -n "$(find "$unpacked" -name '_filters_rs*' -type f)" ]]; then
  echo "wheel-sbom: wheel bundles the _filters_rs extension but the SBOM lists no" \
    "extension crates; did maturin embed dist-info/sboms?" >&2
  exit 1
fi
echo "wheel-sbom: wrote $output"
