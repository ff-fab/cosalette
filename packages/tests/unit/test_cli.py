"""Tests for cosalette._cli — CLI scaffolding.

Test Techniques Used:
    - Specification-based Testing: CLI flag parsing and defaults
    - State-based Testing: Verifying settings/dry_run propagation
    - Error Condition Testing: Invalid flag values, config errors
    - Behavioural Testing: Exit codes and output text
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from cosalette._app import App
from cosalette._cli import EXIT_CONFIG_ERROR, EXIT_OK, EXIT_RUNTIME_ERROR, build_cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> App:
    """Minimal App instance for CLI tests."""
    return App(name="testapp", version="1.0.0", description="Test application")


@pytest.fixture
def runner() -> CliRunner:
    """Typer CLI test runner."""
    return CliRunner()


# ---------------------------------------------------------------------------
# TestVersionFlag
# ---------------------------------------------------------------------------


class TestVersionFlag:
    """--version flag tests.

    Technique: Specification-based Testing — verifying the version
    callback prints correct output and exits cleanly.
    """

    def test_version_prints_name_and_version(self, app: App, runner: CliRunner) -> None:
        """--version prints '{name} v{version}' and exits 0."""
        cli = build_cli(app)

        result = runner.invoke(cli, ["--version"])

        assert result.exit_code == EXIT_OK
        assert "testapp v1.0.0" in result.output


# ---------------------------------------------------------------------------
# TestHelpFlag
# ---------------------------------------------------------------------------


class TestHelpFlag:
    """--help flag tests.

    Technique: Specification-based Testing — verifying help text content.
    """

    def test_help_shows_description_and_powered_by(
        self, app: App, runner: CliRunner
    ) -> None:
        """--help includes the description and 'powered by cosalette'."""
        cli = build_cli(app)

        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == EXIT_OK
        assert "Test application" in result.output
        assert "powered by cosalette" in result.output

    def test_help_shows_all_expected_options(self, app: App, runner: CliRunner) -> None:
        """--help lists all framework-level options."""
        cli = build_cli(app)

        result = runner.invoke(cli, ["--help"])

        assert "--version" in result.output
        assert "--dry-run" in result.output
        assert "--log-level" in result.output
        assert "--log-format" in result.output
        assert "--env-file" in result.output


# ---------------------------------------------------------------------------
# TestDryRunFlag
# ---------------------------------------------------------------------------


class TestDryRunFlag:
    """--dry-run flag tests.

    Technique: State-based Testing — verifying the flag propagates
    to App._dry_run.
    """

    def test_dry_run_flag_sets_app_dry_run(self, app: App, runner: CliRunner) -> None:
        """--dry-run sets app._dry_run = True."""
        cli = build_cli(app)

        with patch.object(app, "_run_async", new_callable=AsyncMock) as mock_run:
            result = runner.invoke(cli, ["--dry-run"])

        assert result.exit_code == EXIT_OK
        assert app._dry_run is True
        mock_run.assert_awaited_once()

    def test_default_dry_run_is_false(self, app: App, runner: CliRunner) -> None:
        """Without --dry-run, app._dry_run stays False."""
        cli = build_cli(app)

        with patch.object(app, "_run_async", new_callable=AsyncMock):
            result = runner.invoke(cli, [])

        assert result.exit_code == EXIT_OK
        assert app._dry_run is False


# ---------------------------------------------------------------------------
# TestEnvFileFlag
# ---------------------------------------------------------------------------


class TestEnvFileFlag:
    """--env-file flag tests.

    Technique: State-based Testing — verifying the env file path
    is forwarded to Settings instantiation.
    """

    def test_env_file_flag_changes_settings_source(
        self, app: App, runner: CliRunner
    ) -> None:
        """--env-file passes the custom path to Settings."""
        cli = build_cli(app)
        mock_settings_cls = MagicMock(wraps=app._settings_class)

        with patch.object(app, "_run_async", new_callable=AsyncMock) as mock_run:
            app._settings_class = mock_settings_cls
            result = runner.invoke(cli, ["--env-file", "custom.env"])

        assert result.exit_code == EXIT_OK
        mock_settings_cls.assert_called_once_with(_env_file="custom.env")
        mock_run.assert_awaited_once()

    def test_default_env_file_is_dot_env(self, app: App, runner: CliRunner) -> None:
        """Default --env-file is '.env'."""
        cli = build_cli(app)
        mock_settings_cls = MagicMock(wraps=app._settings_class)

        with patch.object(app, "_run_async", new_callable=AsyncMock) as mock_run:
            app._settings_class = mock_settings_cls
            result = runner.invoke(cli, [])

        assert result.exit_code == EXIT_OK
        mock_settings_cls.assert_called_once_with(_env_file=".env")
        mock_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestLogLevelOverride
# ---------------------------------------------------------------------------


class TestLogLevelOverride:
    """--log-level override tests.

    Technique: State-based Testing — verifying the settings.logging.level
    is overridden when --log-level is provided.
    """

    def test_log_level_overrides_settings(self, app: App, runner: CliRunner) -> None:
        """--log-level DEBUG overrides settings.logging.level."""
        cli = build_cli(app)
        captured_settings = {}

        async def capture_settings(*, settings=None, **kwargs):  # noqa: ANN001, ANN003
            captured_settings["settings"] = settings

        with patch.object(app, "_run_async", side_effect=capture_settings):
            result = runner.invoke(cli, ["--log-level", "DEBUG"])

        assert result.exit_code == EXIT_OK
        assert captured_settings["settings"].logging.level == "DEBUG"

    def test_invalid_log_level_returns_error(self, app: App, runner: CliRunner) -> None:
        """Invalid --log-level (e.g., 'INVALID') exits with non-zero."""
        cli = build_cli(app)

        result = runner.invoke(cli, ["--log-level", "INVALID"])

        assert result.exit_code != EXIT_OK


# ---------------------------------------------------------------------------
# TestLogFormatOverride
# ---------------------------------------------------------------------------


class TestLogFormatOverride:
    """--log-format override tests.

    Technique: State-based Testing — verifying the settings.logging.format
    is overridden when --log-format is provided.
    """

    def test_log_format_overrides_settings(self, app: App, runner: CliRunner) -> None:
        """--log-format text overrides settings.logging.format."""
        cli = build_cli(app)
        captured_settings = {}

        async def capture_settings(*, settings=None, **kwargs):  # noqa: ANN001, ANN003
            captured_settings["settings"] = settings

        with patch.object(app, "_run_async", side_effect=capture_settings):
            result = runner.invoke(cli, ["--log-format", "text"])

        assert result.exit_code == EXIT_OK
        assert captured_settings["settings"].logging.format == "text"

    def test_invalid_log_format_returns_error(
        self, app: App, runner: CliRunner
    ) -> None:
        """Invalid --log-format exits with non-zero."""
        cli = build_cli(app)

        result = runner.invoke(cli, ["--log-format", "yaml"])

        assert result.exit_code != EXIT_OK


# ---------------------------------------------------------------------------
# TestExitCodes
# ---------------------------------------------------------------------------


class TestExitCodes:
    """Exit code tests.

    Technique: Behavioural Testing — verifying correct exit codes
    for various scenarios.
    """

    def test_clean_run_exits_zero(self, app: App, runner: CliRunner) -> None:
        """Successful run returns exit code 0."""
        cli = build_cli(app)

        with patch.object(app, "_run_async", new_callable=AsyncMock):
            result = runner.invoke(cli, [])

        assert result.exit_code == EXIT_OK

    def test_config_error_exits_one(self, runner: CliRunner) -> None:
        """Configuration validation error returns exit code 1."""
        from pydantic_settings import BaseSettings

        # Create an App with a settings class that always raises
        class BadSettings(BaseSettings):
            required_field: str  # no default → validation error

        bad_app = App(
            name="badapp",
            version="0.0.1",
            settings_class=BadSettings,  # type: ignore[arg-type]
        )
        cli = build_cli(bad_app)

        result = runner.invoke(cli, [])

        assert result.exit_code == EXIT_CONFIG_ERROR

    def test_exit_code_constants_have_expected_values(self) -> None:
        """Exit code constants match documented values."""
        assert EXIT_OK == 0
        assert EXIT_CONFIG_ERROR == 1
        assert EXIT_RUNTIME_ERROR == 3

    def test_runtime_error_exits_three(self, app: App, runner: CliRunner) -> None:
        """Unhandled exception in _run_async returns exit code 3."""
        cli = build_cli(app)

        async def boom(**kwargs: object) -> None:  # noqa: ARG001
            raise RuntimeError("kaboom")

        with patch.object(app, "_run_async", side_effect=boom):
            result = runner.invoke(cli, [])

        assert result.exit_code == EXIT_RUNTIME_ERROR
