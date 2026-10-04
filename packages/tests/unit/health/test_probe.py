"""Unit tests for the stdlib-only ``cosalette-health`` fallback probe.

ADR-087: platform wheels ship ``cosalette-health`` as a native binary; the
sdist installs :func:`cosalette._health._probe.main` under the same name.
Both implement the health file contract (ADR-083), pinned by the golden
cases in ``packages/tests/fixtures/health_file_cases.json`` that the Rust
probe's ``cargo test`` reads too.

Test Techniques Used:
    - Specification-based Testing: golden contract cases shared with Rust
    - Boundary Value Analysis: --max-age at and just over the file age, 0
    - Equivalence Partitioning: --max-age values (valid, negative, NaN,
      infinite, non-numeric); file source (--file, env var, neither)
    - Error Guessing: usage errors must exit 1 (argparse would exit 2),
      abbreviated options, a closed configuration
    - Specification-based Testing: the module imports only the stdlib
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from cosalette._health._probe import (
    DEFAULT_FAIL_ON,
    HEALTH_FILE_ENV,
    check_health_file,
    main,
)

pytestmark = pytest.mark.unit

CASES_FILE = Path(__file__).parents[2] / "fixtures" / "health_file_cases.json"
CASES: list[dict[str, Any]] = json.loads(CASES_FILE.read_text(encoding="utf-8"))[
    "cases"
]


class TestGoldenCases:
    """The Python check agrees with the shared contract fixtures."""

    @pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
    def test_case(self, tmp_path: Path, case: dict[str, Any]) -> None:
        """Technique: Specification-based — one golden case per contract rule."""
        # Arrange
        path = tmp_path / "health.json"
        if "text" in case:
            path.write_text(case["text"], encoding="utf-8")
        elif "json" in case:
            path.write_text(json.dumps(case["json"]), encoding="utf-8")

        # Act
        result = check_health_file(
            path,
            now=case["now"],
            max_age=case.get("max_age"),
            fail_on=case.get("fail_on", DEFAULT_FAIL_ON),
        )

        # Assert
        assert result.healthy is case["healthy"]
        if "reason" in case:
            assert result.reason == case["reason"].replace("{path}", str(path))
        else:
            prefix = case["reason_prefix"].replace("{path}", str(path))
            assert result.reason.startswith(prefix)


def _health_file(tmp_path: Path, age: float, **extra: Any) -> Path:
    path = tmp_path / "health.json"
    data = {"written_at": time.time() - age, "interval": 10, **extra}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestMain:
    """``main`` maps the check to exit codes and output like the binary."""

    @pytest.fixture(autouse=True)
    def _no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(HEALTH_FILE_ENV, raising=False)

    def test_fresh_file_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _health_file(tmp_path, age=1)

        code = main(["--file", str(path)])

        out, err = capsys.readouterr()
        assert code == 0
        assert out.startswith("healthy (health file ")
        assert err == ""

    def test_env_var_supplies_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _health_file(tmp_path, age=1)
        monkeypatch.setenv(HEALTH_FILE_ENV, f" {path} ")

        assert main([]) == 0

    def test_file_option_wins_over_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _health_file(tmp_path, age=1)
        monkeypatch.setenv(HEALTH_FILE_ENV, str(tmp_path / "missing.json"))

        assert main(["--file", str(path)]) == 0

    @pytest.mark.parametrize(
        ("argv", "env"),
        [([], None), ([], "  "), (["--file="], None)],
        ids=["unset", "blank-env", "empty-option"],
    )
    def test_no_file_configured_exits_one(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        argv: list[str],
        env: str | None,
    ) -> None:
        if env is not None:
            monkeypatch.setenv(HEALTH_FILE_ENV, env)

        code = main(argv)

        assert code == 1
        assert capsys.readouterr().err == (
            f"unhealthy: no health file (pass --file or set {HEALTH_FILE_ENV})\n"
        )

    def test_missing_file_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "missing.json"

        code = main(["--file", str(path)])

        assert code == 1
        assert capsys.readouterr().err == (
            f"unhealthy: health file {path} does not exist\n"
        )

    @pytest.mark.parametrize(
        ("max_age", "code"),
        [("105", 0), ("95", 1)],
        ids=["above-age", "below-age"],
    )
    def test_max_age_option(self, tmp_path: Path, max_age: str, code: int) -> None:
        """Technique: BVA — --max-age either side of a 100s-old file."""
        path = _health_file(tmp_path, age=100)

        assert main(["--file", str(path), f"--max-age={max_age}"]) == code

    def test_fail_on_is_repeatable(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        devices = {"a": {"status": "stale"}, "b": {"status": "error"}}
        path = _health_file(tmp_path, age=1, devices=devices)

        code = main(["--file", str(path), "--fail-on", "error", "--fail-on", "stale"])

        assert code == 1
        assert capsys.readouterr().err == (
            "unhealthy: failing devices: a=stale, b=error\n"
        )

    @pytest.mark.parametrize(
        "argv",
        [
            ["--bogus"],
            ["positional"],
            ["--file"],
            ["--max-age"],
            ["--fail-on"],
            ["--max-age", "-1"],
            ["--max-age", "abc"],
            ["--max-age", "inf"],
            ["--max-age", "nan"],
            ["--max", "5"],
            ["--file", "--max-age"],
        ],
        ids=lambda argv: " ".join(argv),
    )
    def test_usage_error_exits_one(
        self, capsys: pytest.CaptureFixture[str], argv: list[str]
    ) -> None:
        """Technique: Error Guessing — argparse would exit 2, Docker's reserved code."""
        with pytest.raises(SystemExit) as exc_info:
            main(argv)

        assert exc_info.value.code == 1
        assert capsys.readouterr().err.startswith("unhealthy: usage error: ")

    def test_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])

        assert exc_info.value.code == 0
        assert capsys.readouterr().out.startswith("usage: cosalette-health")


class TestImports:
    """The fallback stays cheap: standard library only."""

    def test_imports_only_the_standard_library(self) -> None:
        """Technique: Specification-based — no third-party module is loaded."""
        # Arrange
        script = (
            "import sys; before = set(sys.modules); "
            "import cosalette._health._probe; "
            "print(*sorted(set(sys.modules) - before), sep='\\n')"
        )

        # Act
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        assert result.returncode == 0, result.stderr
        loaded = {name.partition(".")[0] for name in result.stdout.split()}
        third_party = loaded - set(sys.stdlib_module_names) - {"cosalette"}
        assert third_party == set()
