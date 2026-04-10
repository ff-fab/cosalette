"""Tests for cosalette._schema_cli — schema validation and tooling.

Test Techniques Used:
    - Specification-based Testing: Command parsing and validation flows
    - State-based Testing: Schema loading and filtering operations
    - Error Condition Testing: Invalid schemas, missing files, app names
    - Behavioural Testing: Exit codes and YAML output formatting
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosalette._schema_cli import EXIT_CONFIG_ERROR, EXIT_OK, schema_app

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    """Typer CLI test runner."""
    return CliRunner()


@pytest.fixture
def schemas_dir() -> Path:
    """Path to test fixture schemas directory."""
    return Path(__file__).parent.parent / "fixtures" / "schemas"


@pytest.fixture
def valid_basic_schema(schemas_dir: Path) -> Path:
    """Path to valid single-app schema."""
    return schemas_dir / "valid_basic.yaml"


@pytest.fixture
def network_schema(schemas_dir: Path) -> Path:
    """Path to network-level schema."""
    return schemas_dir / "network_basic.yaml"


@pytest.fixture
def invalid_schema(schemas_dir: Path) -> Path:
    """Path to invalid schema."""
    return schemas_dir / "invalid_version.yaml"


# ---------------------------------------------------------------------------
# Tests for validate command
# ---------------------------------------------------------------------------


class TestValidateCommand:
    """Test suite for schema validate command.

    Validates command parsing, schema loading, and success/error reporting
    for various schema document types.
    """

    def test_validate_valid_schema(
        self,
        runner: CliRunner,
        valid_basic_schema: Path,
    ) -> None:
        """Should succeed and show summary for valid single-app schema.

        Test Boundary: Command interface for valid schema documents.
        Test Technique: Specification-based testing of success path.
        """
        result = runner.invoke(schema_app, ["validate", str(valid_basic_schema)])

        assert result.exit_code == EXIT_OK
        assert "Schema validated: vito2mqtt v0.2.0" in result.stdout
        assert "AsyncAPI version: 3.0.0" in result.stdout
        assert "Channels:" in result.stdout
        assert "single-app" in result.stdout

    def test_validate_network_schema(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should succeed and show network details for network-level schema.

        Test Boundary: Command interface for network schemas.
        Test Technique: Specification-based testing of network schema handling.
        """
        result = runner.invoke(schema_app, ["validate", str(network_schema)])

        assert result.exit_code == EXIT_OK
        assert "Schema validated:" in result.stdout
        assert "Smart Home MQTT Network v2.1.0" in result.stdout
        assert "AsyncAPI version: 3.0.0" in result.stdout
        assert "Apps in network:" in result.stdout
        assert "network-level" in result.stdout

    def test_validate_invalid_schema(
        self,
        runner: CliRunner,
        invalid_schema: Path,
    ) -> None:
        """Should fail with error for invalid schema document.

        Test Boundary: Command interface for invalid schema documents.
        Test Technique: Error condition testing.
        """
        result = runner.invoke(schema_app, ["validate", str(invalid_schema)])

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "Failed to load schema" in result.stderr

    def test_validate_missing_file(self, runner: CliRunner) -> None:
        """Should fail with error for non-existent file.

        Test Boundary: Command interface file validation.
        Test Technique: Error condition testing for missing files.
        """
        nonexistent = Path("/nonexistent/schema.yaml")
        result = runner.invoke(schema_app, ["validate", str(nonexistent)])

        # Typer handles file existence validation and exits with code 2
        assert result.exit_code == 2
        assert "does not exist" in result.stderr


# ---------------------------------------------------------------------------
# Tests for slice command
# ---------------------------------------------------------------------------


class TestSliceCommand:
    """Test suite for schema slice command.

    Validates network schema filtering, app extraction, and YAML output
    generation for various network configurations.
    """

    def test_slice_extracts_app_channels(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should extract only channels for specified app.

        Test Boundary: Schema filtering for valid app name.
        Test Technique: State-based testing of channel extraction.
        """
        result = runner.invoke(
            schema_app,
            ["slice", "--network", str(network_schema), "--app", "vito2mqtt"],
        )

        assert result.exit_code == EXIT_OK

        # Should contain YAML output
        output = result.stdout
        assert "asyncapi: 3.0.0" in output
        assert "title: vito2mqtt" in output
        assert "version: 2.1.0" in output

        # Should contain vito2mqtt channels
        assert "vitoTemperature:" in output
        assert "vitoValveCommand:" in output
        assert "vito2mqtt/temperature/state" in output
        assert "vito2mqtt/valve/set" in output

        # Should not contain airthings channels (different app)
        assert "airthings" not in output.lower()

        # Should contain all_apps scoped channels
        assert "appStatus:" in output
        assert "{appName}/status" in output

    def test_slice_non_network_schema(
        self,
        runner: CliRunner,
        valid_basic_schema: Path,
    ) -> None:
        """Should fail when trying to slice non-network schema.

        Test Boundary: Schema type validation for slice operation.
        Test Technique: Error condition testing for wrong schema type.
        """
        result = runner.invoke(
            schema_app,
            ["slice", "--network", str(valid_basic_schema), "--app", "vito2mqtt"],
        )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "not network-level" in result.stderr

    def test_slice_unknown_app(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should fail when app name not found in schema.

        Test Boundary: App name validation for slice operation.
        Test Technique: Error condition testing for missing app reference.
        """
        result = runner.invoke(
            schema_app,
            ["slice", "--network", str(network_schema), "--app", "nonexistent"],
        )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "App 'nonexistent' not found" in result.stderr
        assert "Available apps:" in result.stderr
        assert "vito2mqtt" in result.stderr
        assert "airthings2mqtt" in result.stderr
