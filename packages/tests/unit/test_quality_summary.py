"""Unit tests for scripts/quality-summary.sh.

Test Techniques Used:
- Specification-Based Testing: verifies the JSON output contract (field names,
  types, and overall pass/fail logic) documented in the script's inline spec.
- Faking/Stubbing: replaces the real ``task`` binary with a controlled fake via
  PATH manipulation so tests run without project tooling installed.
- Error Guessing: parametrized failure scenarios cover lint, typecheck, and
  test-suite failures independently.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Depth breakdown: unit/ → tests/ → packages/ → <project root>
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _PROJECT_ROOT / "scripts" / "quality-summary.sh"


@pytest.fixture(autouse=True)
def _require_jq() -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq is required by quality-summary.sh")


def _make_env(
    tmp_path: Path,
    *,
    lint_rc: int = 0,
    typecheck_rc: int = 0,
    test_summary: str = "2 passed, 1 skipped",
    test_rc: int = 0,
) -> dict[str, str]:
    """Create a fake ``task`` stub in *tmp_path/bin* and return a modified env."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stub = fake_bin / "task"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'case "$1" in\n'
        "  lint)\n"
        "    echo 'lint output'\n"
        f"    exit {lint_rc}\n"
        "    ;;\n"
        "  typecheck)\n"
        "    echo 'typecheck output'\n"
        f"    exit {typecheck_rc}\n"
        "    ;;\n"
        "  test:unit)\n"
        f"    echo '================== {test_summary} in 0.10s =================='\n"
        f"    exit {test_rc}\n"
        "    ;;\n"
        "  *)\n"
        '    echo "unexpected task: $1" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    return env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_all_checks_pass(tmp_path: Path) -> None:
    result = _run(_make_env(tmp_path, test_summary="2 passed, 1 skipped"))

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["overall"] == "PASS"
    assert payload["lint"]["passed"] is True
    assert payload["typecheck"]["passed"] is True
    assert payload["tests"]["total"] == 3
    assert payload["tests"]["passed_count"] == 2
    assert payload["tests"]["skipped"] == 1
    assert payload["tests"]["failed_count"] == 0


@pytest.mark.parametrize(
    ("lint_rc", "typecheck_rc", "test_summary", "test_rc", "check_key"),
    [
        (1, 0, "2 passed in 0.10s", 0, "lint"),
        (0, 1, "2 passed in 0.10s", 0, "typecheck"),
        (0, 0, "1 failed, 1 passed", 1, "tests"),
    ],
    ids=["lint-failure", "typecheck-failure", "test-failure"],
)
def test_check_failure_reports_fail(
    tmp_path: Path,
    lint_rc: int,
    typecheck_rc: int,
    test_summary: str,
    test_rc: int,
    check_key: str,
) -> None:
    env = _make_env(
        tmp_path,
        lint_rc=lint_rc,
        typecheck_rc=typecheck_rc,
        test_summary=test_summary,
        test_rc=test_rc,
    )
    result = _run(env)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "FAIL"
    assert payload[check_key]["passed"] is False
