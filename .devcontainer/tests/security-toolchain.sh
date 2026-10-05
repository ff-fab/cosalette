#!/usr/bin/env bash
# Run as the development user inside the rebuilt image.
set -euo pipefail
scratch="$(mktemp -d)"
trap 'rm -rf "${scratch}"' EXIT

uv --version
black --version
pytest --version
bd version
curl --version >/dev/null
uv run --no-project --no-python-downloads --python /usr/local/bin/python python -c '
from importlib.util import find_spec
from pathlib import Path
import sys
assert sys.version_info[:2] == (3, 14)
assert find_spec("pip") is None
assert find_spec("virtualenv") is None
assert not list(Path("/usr/local/py-utils").rglob("pip/_vendor/vendor.txt"))
assert not Path("/usr/local/py-utils/venvs/pipenv").exists()
assert not Path("/usr/local/py-utils/venvs/virtualenv").exists()
'
for package in python3.13 libpython3.13-minimal libpython3.13-stdlib python3-urllib3; do
    status="$(dpkg-query -W -f='${db:Status-Status}' "${package}" 2>/dev/null || true)"
    if [ "${status}" = installed ]; then
        echo "Unexpected retained vulnerable distro package: ${package}" >&2
        exit 1
    fi
done
if dpkg-query -W -f='${Package} ${db:Status-Status}\n' \
    | grep -E '^linux-(image|modules)[^ ]* installed$'; then
    echo 'Executable kernel package requires a separate image assessment' >&2
    exit 1
fi
while IFS= read -r path; do
    if [ -f "${path}" ] && file -b "${path}" | grep -q '^ELF'; then
        echo "Unexpected executable object in header package: ${path}" >&2
        exit 1
    fi
done < <(dpkg-query -L linux-libc-dev)

printf '#include <linux/types.h>\n#include <stdio.h>\nint main(void){puts("native C OK");return 0;}\n' \
    | cc -x c -o "${scratch}/c-smoke" -
"${scratch}/c-smoke"
printf 'fn main() { println!("native Rust OK"); }\n' \
    | rustc --edition=2024 -o "${scratch}/rust-smoke" -
"${scratch}/rust-smoke"
uv venv "${scratch}/venv"
echo 'Devcontainer package removal and native toolchain checks passed'
