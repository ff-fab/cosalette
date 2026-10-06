#!/usr/bin/env bash
# Bundle the native cosalette-health probe into the next platform wheel (ADR-087).
#
# Usage: bash scripts/bundle-health-probe.sh [TARGET]
#
# Builds crates/cosalette-health for TARGET (default: the host) with
# cargo-auditable, which embeds the crate dependency list in the binary so
# syft/grype can report it from the wheel (ADR-017), stages the binary as
# packages/data/scripts/cosalette-health[.exe], and edits pyproject.toml so
# the wheel ships it as its only cosalette-health:
#   - adds `data = "packages/data"` under [tool.maturin] (wheel .data/scripts),
#   - removes the Python fallback console script of the same name, which
#     installers would otherwise write over the binary.
# Run it only for platform wheels, in a disposable checkout (CI) or via
# `task build:wheel:probe`, which restores pyproject.toml afterwards. The
# committed pyproject.toml builds the sdist with the Python fallback.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

target="${1:-}"
target_args=()
if [[ -n "$target" ]]; then
  target_args=(--target "$target")
fi

if ! cargo auditable --version >/dev/null 2>&1; then
  echo "bundle-health-probe: cargo-auditable is required (cargo install cargo-auditable --locked)" >&2
  exit 1
fi
cargo auditable build --locked --profile probe -p cosalette-health ${target_args[@]+"${target_args[@]}"}

exe=""
if [[ "$target" == *windows* || ( -z "$target" && "${OS:-}" == "Windows_NT" ) ]]; then
  exe=".exe"
fi
out_dir="${CARGO_TARGET_DIR:-target}${target:+/$target}/probe"
binary="$out_dir/cosalette-health$exe"
if [[ ! -f "$binary" ]]; then
  echo "bundle-health-probe: built binary not found at $binary" >&2
  exit 1
fi

rm -rf packages/data
mkdir -p packages/data/scripts
cp "$binary" "packages/data/scripts/cosalette-health$exe"
chmod 755 "packages/data/scripts/cosalette-health$exe"

# Portable in-place edits (GNU and BSD sed); idempotent.
sed -i.bak '/^cosalette-health = /d' pyproject.toml
if ! grep -q '^data = "packages/data"$' pyproject.toml; then
  sed -i.bak 's|^\[tool\.maturin\]$|[tool.maturin]\
data = "packages/data"|' pyproject.toml
fi
rm -f pyproject.toml.bak

if grep -q '^cosalette-health = ' pyproject.toml ||
  ! grep -q '^data = "packages/data"$' pyproject.toml; then
  echo "bundle-health-probe: failed to update pyproject.toml" >&2
  exit 1
fi
echo "bundle-health-probe: staged $binary as packages/data/scripts/cosalette-health$exe"
