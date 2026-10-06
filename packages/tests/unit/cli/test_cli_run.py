"""Tests for cosalette._cli_run — the Typer-free run path of ``App.cli()``.

Test Techniques Used:
    - Decision Table: argv shapes the fast path accepts versus the ones it
      hands to Typer (subcommands, help, version, unknown or malformed
      options, invalid log values, shell completion).
    - Back-to-back Testing: the fast path and the Typer CLI turn the same
      argv into the same dry-run flag and settings.
    - Equivalence Partitioning: a fresh interpreter running a plain argv
      never imports Typer.

See Also:
    cos-8jxg.4 — run path without building the Typer CLI; ADR-005 amendment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosalette._app import App
from cosalette._cli import build_cli
from cosalette._cli_run import RunArgs, parse_run_args
from cosalette._settings import Settings
from cosalette.testing._settings import _IsolatedSettings

pytestmark = pytest.mark.unit


@pytest.fixture
def app() -> App:
    return App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)


class TestParseRunArgs:
    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            ([], RunArgs()),
            (["--dry-run"], RunArgs(dry_run=True)),
            (["--log-level", "debug"], RunArgs(log_level="debug")),
            (["--log-format=json"], RunArgs(log_format="json")),
            (
                ["--env-file", "a.env", "--config-file=c.toml", "--dry-run"],
                RunArgs(dry_run=True, env_file="a.env", config_file="c.toml"),
            ),
            (["--log-level", "INFO", "--log-level=ERROR"], RunArgs(log_level="ERROR")),
        ],
    )
    def test_accepts_run_options(self, argv: list[str], expected: RunArgs) -> None:
        assert parse_run_args(argv) == expected

    @pytest.mark.parametrize(
        "argv",
        [
            ["--help"],
            ["--version"],
            ["--show-devices"],
            ["--show-devices-json"],
            ["--install-completion"],
            ["schema", "dump"],
            ["health"],
            ["--dry-run", "health"],
            ["--nope"],
            ["--dry-run=true"],
            ["--log-level"],
            ["--log-level", "--dry-run"],
            ["--log-level", "BOGUS"],
            ["--log-format=xml"],
            ["--"],
            ["--env-file="],
            ["--config-file="],
            ["--env-file", ""],
            ["--config-file", ""],
            ["--log-level="],
            ["--log-format="],
        ],
    )
    def test_hands_other_argv_to_typer(self, argv: list[str]) -> None:
        assert parse_run_args(argv) is None

    def test_shell_completion_goes_to_typer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("_TESTAPP_COMPLETE", "bash_complete")

        assert parse_run_args([]) is None


class TestParityWithTyper:
    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["--dry-run"],
            ["--log-level", "debug", "--log-format=json"],
            ["--log-level=warning", "--log-level", "error"],
        ],
    )
    def test_same_dry_run_and_settings(
        self, app: App, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        runs: list[tuple[bool, Settings]] = []
        monkeypatch.setattr(
            "cosalette._cli_run.run_app",
            lambda a, settings: runs.append((a._dry_run, settings)),
        )
        monkeypatch.setattr("sys.argv", ["testapp", *argv])

        # Act
        app.cli()
        app._dry_run = False
        CliRunner().invoke(build_cli(app), argv, catch_exceptions=False)

        # Assert
        (fast_dry, fast), (typer_dry, typed) = runs
        assert fast_dry == typer_dry
        assert fast.logging == typed.logging

    def test_missing_env_file_exits_with_config_error(
        self,
        app: App,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        missing = str(tmp_path / "missing.env")
        monkeypatch.setattr("sys.argv", ["testapp", "--env-file", missing])

        with pytest.raises(SystemExit) as exc_info:
            app.cli()
        typer_result = CliRunner().invoke(build_cli(app), ["--env-file", missing])

        assert exc_info.value.code == typer_result.exit_code == 1
        assert capsys.readouterr().err == typer_result.stderr


def test_plain_run_does_not_import_typer() -> None:
    """Technique: Equivalence Partitioning — fresh interpreter, plain argv."""
    script = """
import sys
import cosalette, cosalette._cli_run
cosalette._cli_run.run_app = lambda app, settings: None
sys.argv = ["demo", "--dry-run", "--log-level", "debug"]
cosalette.App(name="demo", version="1.0").cli()
print(*sorted(sys.modules), sep="\\n")
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    loaded = set(result.stdout.split())
    assert loaded.isdisjoint(
        {"typer", "click", "cosalette._cli", "cosalette._schema._cli"}
    )
