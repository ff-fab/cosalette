#!/usr/bin/env bash
# Technique: Equivalence Partitioning / Error Guessing — remapped UIDs, an
# inherited CODEX_HOME, and a symlink that root must not chown through.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../codex-home.sh"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
id() {
    case "$1" in
        -u) echo "$developer_uid" ;;
        -g) echo 2345 ;;
        *) return 1 ;;
    esac
}
sudo() { printf '%s\n' "$*" >> "$scratch/calls"; }

for developer_uid in 1000 1234; do
    : > "$scratch/calls"
    HOME="$scratch/home" CODEX_HOME="" prepare_codex_home
    grep -Fx "install -d -m 0700 -o $developer_uid -g 2345 $scratch/home/.codex" "$scratch/calls" >/dev/null
done

: > "$scratch/calls"
CODEX_HOME="$scratch/custom" prepare_codex_home
grep -Fx "install -d -m 0700 -o 1234 -g 2345 $scratch/custom" "$scratch/calls" >/dev/null

: > "$scratch/calls"
ln -s /etc "$scratch/link"
if CODEX_HOME="$scratch/link" prepare_codex_home 2>/dev/null; then
    echo "symlinked CODEX_HOME was not refused" >&2
    exit 1
fi
[[ ! -s "$scratch/calls" ]]
printf 'codex home: UID ownership, CODEX_HOME override and symlink guard OK\n'
