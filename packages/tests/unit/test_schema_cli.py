"""Tests for cosalette._schema_cli — schema validation and tooling.

Test Techniques Used:
    - Specification-based Testing: Command parsing and validation flows
    - State-based Testing: Schema loading and filtering operations
    - Error Condition Testing: Invalid schemas, missing files, app names
    - Behavioural Testing: Exit codes and YAML output formatting
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cosalette._app import App, DeviceContext
from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._schema_cli import _build_snapshot_channel, _to_camel_case, schema_app

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


@pytest.fixture
def scope_violation_schema(schemas_dir: Path) -> Path:
    """Path to network schema with a non-auto-wired all_apps channel."""
    return schemas_dir / "network_scope_violation.yaml"


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


# ---------------------------------------------------------------------------
# Helpers for check command tests
# ---------------------------------------------------------------------------


def _make_app_with_devices(*names: str) -> App:
    """Create a minimal App with the given device names for testing.

    Args:
        *names: Device names to register.

    Returns:
        An App instance with devices registered for each name.
    """
    app = App(name="vito2mqtt", version="0.2.0", description="Test app")
    for name in names:
        # Create a device handler for each name
        @app.device(name)
        async def handler(ctx: DeviceContext) -> None:
            pass

    return app


@pytest.fixture
def mixed_app() -> App:
    """App with one telemetry, one command, and one device registration.

    Covers all three registration kinds so dump/init tests exercise every
    branch of the channel-building logic (send vs receive, state vs set).
    """
    app = App(name="vito2mqtt", version="0.2.0", description="Test app")

    @app.telemetry("temperature", interval=300)
    async def temp_handler() -> dict[str, object]:
        return {}

    @app.command("valve")
    async def valve_handler(ctx: DeviceContext, topic: str, payload: str) -> None:
        pass

    @app.device("sensor")
    async def sensor_handler(ctx: DeviceContext) -> None:
        pass

    return app


# ---------------------------------------------------------------------------
# Tests for check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    """Test suite for schema check command.

    Validates app import, registration extraction, schema validation,
    and output formatting for CI gating workflow.
    """

    def test_check_compliant_app(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should exit 0 when all schema devices are registered.

        Test Boundary: Compliant app validation against network schema.
        Test Technique: State-based testing with mocked app import.
        """
        # Create app with both devices from network_schema: temperature, valve
        test_app = _make_app_with_devices("temperature", "valve")

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "dummy:app",
                    "--schema",
                    str(network_schema),
                ],
            )

        assert result.exit_code == EXIT_OK
        assert "temperature — OK" in result.stdout
        assert "valve — OK" in result.stdout
        assert "0 violations, 2 compliant" in result.stdout
        assert "Exit code: 0" in result.stdout

    def test_check_missing_device(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should exit 1 when schema device is not registered.

        Test Boundary: Non-compliant app with missing device registration.
        Test Technique: Error condition testing with schema violation.
        """
        # Create app missing the "valve" device (only has "temperature")
        test_app = _make_app_with_devices("temperature")

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "dummy:app",
                    "--schema",
                    str(network_schema),
                ],
            )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "valve — MISSING" in result.stdout
        assert "Schema expects device 'valve'" in result.stdout
        assert "temperature — OK" in result.stdout
        assert "1 violations, 1 compliant" in result.stdout
        assert "Exit code: 1" in result.stdout

    def test_check_extra_device(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should exit 0 with warning when app has extra devices.

        Test Boundary: App with additional registrations not in schema.
        Test Technique: State-based testing with warning condition.
        """
        # Create app with schema devices plus an extra one
        test_app = _make_app_with_devices("temperature", "valve", "extra_sensor")

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "dummy:app",
                    "--schema",
                    str(network_schema),
                ],
            )

        assert result.exit_code == EXIT_OK
        assert "temperature — OK" in result.stdout
        assert "valve — OK" in result.stdout
        assert "extra_sensor — EXTRA" in result.stdout
        assert "1 extra, 2 compliant" in result.stdout
        assert "Exit code: 0" in result.stdout

    def test_check_invalid_app_spec(
        self, runner: CliRunner, network_schema: Path
    ) -> None:
        """Should exit 1 for malformed app specification.

        Test Boundary: Command argument validation for app import spec.
        Test Technique: Error condition testing for invalid input format.
        """
        result = runner.invoke(
            schema_app,
            [
                "check",
                "--app",
                "invalid_spec_no_colon",
                "--schema",
                str(network_schema),
            ],
        )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "Invalid app spec" in result.stderr
        assert "Expected format: 'module.path:attribute'" in result.stderr

    def test_check_invalid_module(
        self, runner: CliRunner, network_schema: Path
    ) -> None:
        """Should exit 1 when module cannot be imported.

        Test Boundary: Module import failure handling.
        Test Technique: Error condition testing for import errors.
        """
        result = runner.invoke(
            schema_app,
            [
                "check",
                "--app",
                "nonexistent.module:app",
                "--schema",
                str(network_schema),
            ],
        )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "Could not import module" in result.stderr

    def test_check_compliant_app_acceptance(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should exit 0 for a fully compliant app (acceptance criterion).

        Test Boundary: Full command integration as specified in requirements.
        Test Technique: Acceptance testing for beads task completion.

        Acceptance criterion: ``cosalette schema check --app X:app --schema
        network.yaml`` exits 0 when all schema devices are registered.
        """
        compliant_app = _make_app_with_devices("temperature", "valve")
        with patch("cosalette._schema_cli._import_app", return_value=compliant_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "test.app:app",
                    "--schema",
                    str(network_schema),
                ],
            )
        assert result.exit_code == EXIT_OK, "Compliant app should exit 0"

    def test_check_non_compliant_app_acceptance(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should exit 1 for a non-compliant app (acceptance criterion).

        Test Boundary: Full command integration as specified in requirements.
        Test Technique: Acceptance testing for beads task completion.

        Acceptance criterion: ``cosalette schema check --app X:app --schema
        network.yaml`` exits 1 when schema devices are missing.
        """
        non_compliant_app = _make_app_with_devices("temperature")  # missing valve
        with patch("cosalette._schema_cli._import_app", return_value=non_compliant_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "test.app:app",
                    "--schema",
                    str(network_schema),
                ],
            )
        assert result.exit_code == EXIT_CONFIG_ERROR, "Non-compliant app should exit 1"

    def test_check_scope_violation(
        self,
        runner: CliRunner,
        scope_violation_schema: Path,
    ) -> None:
        """Should exit 1 when a mandatory all_apps channel is unregistered.

        Test Boundary: Scope violation detection for non-auto-wired channels.
        Test Technique: State-based testing — the ``appDiagnostics`` channel
        (scope=all_apps, suffix ``diagnostics``) is NOT in _AUTO_WIRED_SUFFIXES,
        so it must be flagged.  ``appStatus`` (suffix ``status``) is auto-wired
        and should be silently skipped.
        """
        # Register the expected device but NOT the diagnostics channel
        test_app = _make_app_with_devices("temperature")

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "dummy:app",
                    "--schema",
                    str(scope_violation_schema),
                ],
            )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "SCOPE VIOLATION" in result.stdout
        assert "appDiagnostics" in result.stdout
        assert "scope=all_apps" in result.stdout
        # Auto-wired channel should NOT appear as a violation
        assert "appStatus" not in result.stdout
        assert "violations" in result.stdout
        assert "Exit code: 1" in result.stdout

    def test_check_scope_violation_counted_in_summary(
        self,
        runner: CliRunner,
        scope_violation_schema: Path,
    ) -> None:
        """Should count scope violations separately in the summary line.

        Test Boundary: Summary formatting when both missing devices and scope
        violations co-exist.
        Test Technique: Behavioural testing — verify violation count includes
        both missing-device and scope-violation categories.
        """
        # Register NO devices — triggers both a missing-device violation
        # (temperature) AND a scope violation (appDiagnostics)
        test_app = App(name="vito2mqtt", version="0.2.0", description="Test app")

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "dummy:app",
                    "--schema",
                    str(scope_violation_schema),
                ],
            )

        assert result.exit_code == EXIT_CONFIG_ERROR
        # 1 missing device (temperature) + 1 scope violation (appDiagnostics) = 2
        assert "2 violations" in result.stdout
        assert "MISSING" in result.stdout
        assert "SCOPE VIOLATION" in result.stdout


# ---------------------------------------------------------------------------
# Tests for dump command
# ---------------------------------------------------------------------------


class TestDumpCommand:
    """Test suite for schema dump command.

    Validates app import, registry introspection, and AsyncAPI generation
    for the dump workflow.
    """

    def test_dump_produces_valid_asyncapi(
        self, runner: CliRunner, mixed_app: App
    ) -> None:
        """Should produce valid AsyncAPI 3.0.0 from app registrations.

        Test Boundary: Full dump command with mixed registration types.
        Test Technique: State-based testing of AsyncAPI generation.
        """
        with patch("cosalette._schema_cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK

        # Check YAML output structure
        output = result.stdout
        assert "asyncapi: 3.0.0" in output
        assert "title: vito2mqtt" in output
        assert "version: 0.2.0" in output
        assert "channels:" in output
        assert "operations:" in output

    def test_dump_includes_telemetry_channels(
        self, runner: CliRunner, mixed_app: App
    ) -> None:
        """Should map telemetry registrations to send channels.

        Test Boundary: Telemetry-to-channel mapping in AsyncAPI generation.
        Test Technique: Specification-based testing of channel types.
        """
        with patch("cosalette._schema_cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK
        output = result.stdout

        # Should have telemetry state channel
        assert "temperatureState:" in output
        assert "vito2mqtt/temperature/state" in output
        assert "publishTemperatureState:" in output
        assert "action: send" in output

    def test_dump_includes_command_channels(
        self, runner: CliRunner, mixed_app: App
    ) -> None:
        """Should map command registrations to receive channels.

        Test Boundary: Command-to-channel mapping in AsyncAPI generation.
        Test Technique: Specification-based testing of channel types.
        """
        with patch("cosalette._schema_cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK
        output = result.stdout

        # Should have command channel
        assert "valveCommand:" in output
        assert "vito2mqtt/valve/set" in output
        assert "receiveValveCommand:" in output
        assert "action: receive" in output

    def test_dump_invalid_app_spec(self, runner: CliRunner) -> None:
        """Should exit 1 for malformed app specification.

        Test Boundary: Command argument validation for app import spec.
        Test Technique: Error condition testing for invalid input format.
        """
        result = runner.invoke(schema_app, ["dump", "--app", "invalid_spec_no_colon"])

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "Invalid app spec" in result.stderr
        assert "Expected format: 'module.path:attribute'" in result.stderr


# ---------------------------------------------------------------------------
# Tests for init command
# ---------------------------------------------------------------------------


class TestInitCommand:
    """Test suite for schema init command.

    Validates scaffold generation with cosalette extensions for
    the init workflow.
    """

    def test_init_includes_enforcement(self, runner: CliRunner, mixed_app: App) -> None:
        """Should include x-cosalette-enforcement section.

        Test Boundary: Extension scaffolding in AsyncAPI generation.
        Test Technique: Specification-based testing of extensions.
        """
        with patch("cosalette._schema_cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK
        output = result.stdout

        # Should have enforcement config
        assert "x-cosalette-enforcement:" in output
        assert "mode: warn" in output
        assert "on_configure: true" in output
        assert "on_publish: false" in output
        assert "network_level: false" in output

    def test_init_includes_extensions(self, runner: CliRunner, mixed_app: App) -> None:
        """Should include x-cosalette-archetype on channels.

        Test Boundary: Channel extension scaffolding.
        Test Technique: Specification-based testing of archetype extensions.
        """
        with patch("cosalette._schema_cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK
        output = result.stdout

        # Should have archetype extensions
        assert "x-cosalette-archetype: device" in output
        assert "x-cosalette-archetype: telemetry" in output
        assert "x-cosalette-archetype: command" in output

    def test_init_produces_valid_asyncapi(
        self, runner: CliRunner, mixed_app: App
    ) -> None:
        """Should produce valid AsyncAPI structure with extensions.

        Test Boundary: Full init command with extension scaffolding.
        Test Technique: State-based testing of enhanced AsyncAPI generation.
        """
        with patch("cosalette._schema_cli._import_app", return_value=mixed_app):
            result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK
        output = result.stdout

        # Should have valid AsyncAPI structure
        assert "asyncapi: 3.0.0" in output
        assert "title: vito2mqtt" in output
        assert "version: 0.2.0" in output
        assert "channels:" in output
        assert "operations:" in output

        # Should have payload scaffolds
        assert "payload:" in output
        assert "type: object" in output


# ---------------------------------------------------------------------------
# Tests for edge cases and error handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test suite for edge cases across schema CLI commands.

    Validates error handling, input sanitisation, and edge conditions
    that aren't covered by the per-command test classes.
    """

    def test_check_missing_and_extra_combined(
        self,
        runner: CliRunner,
        network_schema: Path,
    ) -> None:
        """Should report both missing and extra devices in summary.

        Test Boundary: Combined violation types in check output.
        Test Technique: Boundary value analysis of summary formatting.
        """
        # Create app with one schema device missing and one extra
        test_app = _make_app_with_devices("temperature", "extra_sensor")

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(
                schema_app,
                [
                    "check",
                    "--app",
                    "dummy:app",
                    "--schema",
                    str(network_schema),
                ],
            )

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "valve — MISSING" in result.stdout
        assert "extra_sensor — EXTRA" in result.stdout
        assert "temperature — OK" in result.stdout
        assert "violations" in result.stdout
        assert "extra" in result.stdout

    def test_import_app_whitespace_spec(self, runner: CliRunner) -> None:
        """Should handle whitespace in app spec gracefully.

        Test Boundary: Input sanitisation for app import specification.
        Test Technique: Error condition testing for whitespace handling.
        """
        result = runner.invoke(
            schema_app,
            ["dump", "--app", "  :  "],
        )

        assert result.exit_code == EXIT_CONFIG_ERROR

    def test_import_app_multiple_colons(
        self,
        runner: CliRunner,
    ) -> None:
        """Should use rightmost colon for module:attr split.

        Test Boundary: Edge case input format with multiple colons.
        Test Technique: Specification-based testing of rsplit behaviour.
        """
        # "a:b:c" should split into module="a:b", attr="c"
        result = runner.invoke(
            schema_app,
            ["dump", "--app", "a:b:c"],
        )

        # This will fail at import (ModuleNotFoundError), but should NOT
        # fail at spec parsing
        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "Could not import module" in result.stderr

    def test_dump_empty_app(self, runner: CliRunner) -> None:
        """Should produce valid AsyncAPI with no channels for empty app.

        Test Boundary: Empty registration set in app introspection.
        Test Technique: Boundary value testing for empty input.
        """
        test_app = App(
            name="empty-app", version="1.0.0", description="No registrations"
        )

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK
        output = result.stdout
        assert "asyncapi: 3.0.0" in output
        assert "title: empty-app" in output
        # No channels section for empty app
        assert "channels:" not in output

    def test_camelcase_operation_names_with_underscores(
        self,
        runner: CliRunner,
    ) -> None:
        """Should produce proper camelCase operation names for underscored devices.

        Test Boundary: AsyncAPI naming convention for multi-word device names.
        Test Technique: Specification-based testing of naming transformation.
        """
        test_app = App(name="testapp", version="1.0.0", description="Test")

        @test_app.device("extra_sensor")
        async def handler(ctx: DeviceContext) -> None:
            pass

        with patch("cosalette._schema_cli._import_app", return_value=test_app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == EXIT_OK
        output = result.stdout
        # Should be "publishExtraSensorState" not "publishExtra_SensorState"
        assert "publishExtraSensorState" in output
        assert "Extra_Sensor" not in output


# ---------------------------------------------------------------------------
# Tests for extracted helpers
# ---------------------------------------------------------------------------


class TestToCamelCase:
    """Edge-case coverage for the ``_to_camel_case`` utility.

    Test Techniques Used:
        - Boundary Value Analysis: empty string, leading/trailing underscores
        - Equivalence Partitioning: underscore patterns, already-capitalised
    """

    @pytest.mark.parametrize(
        ("input_name", "expected"),
        [
            ("temperature", "Temperature"),
            ("extra_sensor", "ExtraSensor"),
            ("a_b_c", "ABC"),
            ("already", "Already"),
            ("UPPER", "Upper"),
            ("a__b", "AB"),
            ("_leading", "Leading"),
            ("trailing_", "Trailing"),
            ("", ""),
        ],
        ids=[
            "simple",
            "underscore",
            "multi_underscore",
            "no_underscore",
            "uppercase",
            "consecutive_underscores",
            "leading_underscore",
            "trailing_underscore",
            "empty",
        ],
    )
    def test_to_camel_case(self, input_name: str, expected: str) -> None:
        """Should convert underscore-separated names to CamelCase."""
        assert _to_camel_case(input_name) == expected


class TestBuildSnapshotChannel:
    """Direct unit tests for ``_build_snapshot_channel``.

    Test Techniques Used:
        - Specification-based Testing: channel type → address/action mapping
        - Decision Coverage: kind parameter branches (device, telemetry, command)
    """

    def test_device_produces_state_channel(self) -> None:
        """Device kind should produce a ``{name}State`` send channel."""
        ch_name, ch_dict, op_name, op_dict = _build_snapshot_channel(
            "myapp", "sensor", kind="device", include_extensions=False
        )

        assert ch_name == "sensorState"
        assert ch_dict["address"] == "myapp/sensor/state"
        assert op_name == "publishSensorState"
        assert op_dict["action"] == "send"
        assert "x-cosalette-archetype" not in ch_dict

    def test_telemetry_produces_state_channel(self) -> None:
        """Telemetry kind should produce a ``{name}State`` send channel."""
        ch_name, ch_dict, op_name, op_dict = _build_snapshot_channel(
            "myapp", "temperature", kind="telemetry", include_extensions=True
        )

        assert ch_name == "temperatureState"
        assert ch_dict["address"] == "myapp/temperature/state"
        assert op_name == "publishTemperatureState"
        assert op_dict["action"] == "send"
        assert ch_dict["x-cosalette-archetype"] == "telemetry"

    def test_command_produces_command_channel(self) -> None:
        """Command kind should produce a ``{name}Command`` receive channel."""
        ch_name, ch_dict, op_name, op_dict = _build_snapshot_channel(
            "myapp", "valve", kind="command", include_extensions=True
        )

        assert ch_name == "valveCommand"
        assert ch_dict["address"] == "myapp/valve/set"
        assert op_name == "receiveValveCommand"
        assert op_dict["action"] == "receive"
        assert ch_dict["x-cosalette-archetype"] == "command"

    def test_underscore_name_produces_camel_operation(self) -> None:
        """Underscored device names should yield CamelCase operation names."""
        _, _, op_name, _ = _build_snapshot_channel(
            "myapp", "extra_sensor", kind="device", include_extensions=False
        )

        assert op_name == "publishExtraSensorState"

    def test_extensions_omitted_when_disabled(self) -> None:
        """``include_extensions=False`` should omit x-cosalette-archetype."""
        _, ch_dict, _, _ = _build_snapshot_channel(
            "myapp", "temp", kind="telemetry", include_extensions=False
        )

        assert "x-cosalette-archetype" not in ch_dict
