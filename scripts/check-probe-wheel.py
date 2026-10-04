"""Check that a platform wheel bundles the native cosalette-health probe (ADR-087).

Usage: python scripts/check-probe-wheel.py WHEEL TARGET

- exactly one ``.data/scripts/cosalette-health[.exe]``, executable on Unix;
- no ``cosalette-health`` console script, which installers would write over
  the binary;
- for ``*-linux-gnu*`` targets, no glibc symbol newer than manylinux2014's 2.17
  (maturin's auditwheel repair only checks the extension module).
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

GLIBC_FLOOR = (2, 17)


def main() -> int:
    wheel, target = Path(sys.argv[1]), sys.argv[2]
    exe = ".exe" if "windows" in target else ""
    errors: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        scripts = [n for n in zf.namelist() if ".data/scripts/" in n]
        if [n.rsplit("/", 1)[1] for n in scripts] != [f"cosalette-health{exe}"]:
            errors.append(f"expected only cosalette-health{exe} in scripts: {scripts}")
        entry_points = next(n for n in zf.namelist() if n.endswith("entry_points.txt"))
        if "cosalette-health" in zf.read(entry_points).decode():
            errors.append("cosalette-health is also a console script")
        if (
            scripts
            and not exe
            and not (zf.getinfo(scripts[0]).external_attr >> 16) & 0o111
        ):
            errors.append(f"{scripts[0]} is not executable")
        if scripts and "-linux-gnu" in target:
            with tempfile.TemporaryDirectory() as tmp:
                binary = Path(zf.extract(scripts[0], tmp))
                readelf = subprocess.run(
                    ["readelf", "-V", "-W", binary],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if readelf.returncode != 0:
                errors.append(f"{scripts[0]} is not an ELF binary")
            symbols = readelf.stdout
            versions = {
                tuple(int(part) for part in found.split("."))
                for found in re.findall(r"GLIBC_(\d+(?:\.\d+)+)", symbols)
            }
            newest = max(versions, default=(0,))
            print(f"newest glibc symbol: {'.'.join(map(str, newest))}")
            if newest > GLIBC_FLOOR:
                errors.append(f"needs glibc {newest}, newer than {GLIBC_FLOOR}")
    for error in errors:
        print(f"error: {wheel.name}: {error}", file=sys.stderr)
    if not errors:
        print(f"{wheel.name}: bundles {scripts[0]}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
