"""Smoke-test the installed ``cosalette-health`` command (ADR-087).

Usage: python scripts/smoke-health-probe.py native|fallback

Run in CI after installing a built wheel (``native``: the bundled Rust
binary) or the sdist (``fallback``: the Python console script). Checks
which kind is on PATH and that it exits 0/1 as the contract says.
Standard library only, so it runs in bare python:3.14 containers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def is_native(probe: Path) -> bool:
    """A binary, not a ``#!`` script or pip's zip-appended .exe launcher."""
    return probe.read_bytes()[:2] != b"#!" and not zipfile.is_zipfile(probe)


def run(probe: Path, *args: str) -> int:
    result = subprocess.run([probe, *args], capture_output=True, text=True, check=False)
    print(
        f"cosalette-health {' '.join(args)} -> {result.returncode}: "
        f"{(result.stdout or result.stderr).strip()}"
    )
    return result.returncode


def main() -> int:
    expected = sys.argv[1]
    found = shutil.which("cosalette-health")
    if found is None:
        print("cosalette-health is not on PATH", file=sys.stderr)
        return 1
    probe = Path(found)
    kind = "native" if is_native(probe) else "fallback"
    print(f"{probe}: {kind}")
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp, "fresh.json")
        stale = Path(tmp, "stale.json")
        fresh.write_text(json.dumps({"written_at": time.time(), "interval": 60}))
        stale.write_text(json.dumps({"written_at": time.time() - 1000}))
        codes = {
            "fresh": run(probe, "--file", str(fresh)),
            "stale": run(probe, "--file", str(stale)),
            "missing": run(probe, "--file", str(Path(tmp, "missing.json"))),
            "usage": run(probe, "--max-age", "-1"),
        }
    expected_codes = {"fresh": 0, "stale": 1, "missing": 1, "usage": 1}
    if kind != expected or codes != expected_codes:
        print(
            f"expected {expected} {expected_codes}, got {kind} {codes}", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
