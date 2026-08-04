"""Tests for cosalette._schema._cli — schema validation and tooling.

Test Techniques Used:
    - Specification-based Testing: Command parsing and validation flows
    - State-based Testing: Schema loading and filtering operations
    - Error Condition Testing: Invalid schemas, missing files, app names
    - Behavioural Testing: Exit codes and YAML output formatting
    - Round-trip Testing: dump/init consumer block parity
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cosalette._app import App, DeviceContext
from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._runners._stream_types import Stream
from cosalette._schema._asyncapi import _to_camel_case
from cosalette._schema._cli import schema_app

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
    return Path(__file__).parent.parent.parent / "fixtures" / "schemas"


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


@pytest.fixture
def consumer_schema(schemas_dir: Path) -> Path:
    """Path to a schema carrying non-ASCII consumer metadata (unit: '°C')."""
    return schemas_dir / "consumer_basic.yaml"


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


@pytest.fixture
def make_app_with_devices() -> Callable[..., App]:
    """Factory fixture: create a minimal App with given device names.

    Args:
        *names: Device names to register.

    Returns:
        An App instance with devices registered for each name.
    """

    def _factory(*names: str) -> App:
        app = App(name="vito2mqtt", version="0.2.0", description="Test app")
        for name in names:

            @app.device(name)
            async def handler(ctx: DeviceContext) -> None:
                pass

        return app

    return _factory


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


@pytest.fixture
def unicode_consumer_app() -> App:
    """App whose telemetry model carries non-ASCII consumer metadata.

    Two properties exercise the docs-quality guarantees of ``schema init``/
    ``dump``: unicode units must survive unescaped, and ``consumer()`` key
    order (``unit`` before ``device_class``) must be preserved rather than
    re-sorted alphabetically by pydantic.
    """
    from pydantic import BaseModel, Field

    from cosalette.schema import consumer

    class Reading(BaseModel):
        temp: Annotated[
            float,
            Field(
                json_schema_extra=consumer(
                    unit="°C",
                    device_class="temperature",
                    state_class="measurement",
                )
            ),
        ]
        radon: Annotated[
            float,
            Field(json_schema_extra=consumer(unit="Bq/m³", device_class="radon")),
        ]

    app = App(name="airthings2mqtt", version="0.1.0", description="Test app")

    @app.telemetry("reading", interval=300, state_model=Reading)
    async def reading_handler() -> dict[str, object]:
        return {}

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
        make_app_with_devices: Callable[..., App],
    ) -> None:
        """Should exit 0 when all schema devices are registered.

        Test Boundary: Compliant app validation against network schema.
        Test Technique: State-based testing with mocked app import.
        """
        # Create app with both devices from network_schema: temperature, valve
        test_app = make_app_with_devices("temperature", "valve")

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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
        make_app_with_devices: Callable[..., App],
    ) -> None:
        """Should exit 1 when schema device is not registered.

        Test Boundary: Non-compliant app with missing device registration.
        Test Technique: Error condition testing with schema violation.
        """
        # Create app missing the "valve" device (only has "temperature")
        test_app = make_app_with_devices("temperature")

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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
        make_app_with_devices: Callable[..., App],
    ) -> None:
        """Should exit 0 with warning when app has extra devices.

        Test Boundary: App with additional registrations not in schema.
        Test Technique: State-based testing with warning condition.
        """
        # Create app with schema devices plus an extra one
        test_app = make_app_with_devices("temperature", "valve", "extra_sensor")

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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
        make_app_with_devices: Callable[..., App],
    ) -> None:
        """Should exit 0 for a fully compliant app (acceptance criterion).

        Test Boundary: Full command integration as specified in requirements.
        Test Technique: Acceptance testing for beads task completion.

        Acceptance criterion: ``cosalette schema check --app X:app --schema
        network.yaml`` exits 0 when all schema devices are registered.
        """
        compliant_app = make_app_with_devices("temperature", "valve")
        with patch("cosalette._schema._cli._import_app", return_value=compliant_app):
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
        make_app_with_devices: Callable[..., App],
    ) -> None:
        """Should exit 1 for a non-compliant app (acceptance criterion).

        Test Boundary: Full command integration as specified in requirements.
        Test Technique: Acceptance testing for beads task completion.

        Acceptance criterion: ``cosalette schema check --app X:app --schema
        network.yaml`` exits 1 when schema devices are missing.
        """
        non_compliant_app = make_app_with_devices("temperature")  # missing valve
        with patch(
            "cosalette._schema._cli._import_app", return_value=non_compliant_app
        ):
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
        make_app_with_devices: Callable[..., App],
    ) -> None:
        """Should exit 1 when a mandatory all_apps channel is unregistered.

        Test Boundary: Scope violation detection for non-auto-wired channels.
        Test Technique: State-based testing — the ``appDiagnostics`` channel
        (scope=all_apps, suffix ``diagnostics``) is NOT in _AUTO_WIRED_SUFFIXES,
        so it must be flagged.  ``appStatus`` (suffix ``status``) is auto-wired
        and should be silently skipped.
        """
        # Register the expected device but NOT the diagnostics channel
        test_app = make_app_with_devices("temperature")

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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

    def test_check_stream_handler_not_reported_as_extra(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Stream handlers must not appear as EXTRA in schema check output.

        Test Boundary: Regression for spurious EXTRA warnings on @app.stream
        handlers — streams are intentionally absent from schema init output
        (dynamic per-sensor topics) and must be excluded from the comparison.
        Test Technique: State-based testing — generate schema via init, then
        check that stream names are silently ignored.
        """
        # Arrange: app with one device (appears in schema) + one stream (does not)
        app = App(name="readings-app", version="1.0.0", description="Test")

        @app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:
            pass

        @app.stream("readings")
        async def _readings(stream: Stream[object]) -> None:
            async for _ in stream:
                pass

        # Act: generate schema via init, write to tmp file
        with patch("cosalette._schema._cli._import_app", return_value=app):
            init_result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])
        assert init_result.exit_code == EXIT_OK
        schema_file = tmp_path / "schema.yaml"
        schema_file.write_text(init_result.stdout, encoding="utf-8")

        # Act: run schema check against generated schema
        with patch("cosalette._schema._cli._import_app", return_value=app):
            check_result = runner.invoke(
                schema_app,
                ["check", "--app", "dummy:app", "--schema", str(schema_file)],
            )

        # Assert: exits 0, device OK, stream silent (no EXTRA)
        assert check_result.exit_code == EXIT_OK
        assert "sensor — OK" in check_result.stdout
        assert "readings — EXTRA" not in check_result.stdout
        assert "EXTRA" not in check_result.stdout

    def test_check_rejects_callable_name_spec(
        self,
        runner: CliRunner,
        callable_name_app: App,
        tmp_path: Path,
    ) -> None:
        """check must exit non-zero for settings-derived apps with unexpanded name=.

        Test Boundary: Guard against phantom qualname comparison — a callable
        name= is only expanded inside app.run(); schema check is settings-free
        and would silently validate against the handler qualname instead of the
        real runtime name, producing a false-green result.
        Test Technique: Error Guessing / negative testing — construct an app
        whose registration has name_spec is not None, supply a valid schema file
        so the --schema exists= check passes, and confirm check refuses early
        with EXIT_CONFIG_ERROR and the ADR-023 / settings-derived message.
        """
        # Arrange: generate a valid schema from a plain literal-name app so that
        # the --schema exists=True guard is satisfied.
        plain_app = App(name="plain-app", version="1.0.0", description="Test")

        @plain_app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:  # type: ignore[type-arg]
            pass

        with patch("cosalette._schema._cli._import_app", return_value=plain_app):
            init_result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])
        assert init_result.exit_code == EXIT_OK
        schema_file = tmp_path / "schema.yaml"
        schema_file.write_text(init_result.stdout, encoding="utf-8")

        # Act: attempt check with the callable-name app
        with patch(
            "cosalette._schema._cli._import_app", return_value=callable_name_app
        ):
            result = runner.invoke(
                schema_app,
                ["check", "--app", "dummy:app", "--schema", str(schema_file)],
            )

        # Assert: guard fires before any comparison
        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "settings-derived" in result.stderr
        assert "ADR-023" in result.stderr
        assert "dynamic_sensor_handler" in result.stderr


@pytest.fixture
def callable_name_app() -> App:
    """App with a device registered via a callable name= (ADR-023 NameSpec).

    Pre-expansion, the registration holds name_spec is not None and .name
    equals the handler qualname.  This simulates an app that requires
    bootstrapping via app.run() before entity names are known.
    """
    app = App(name="dynamic-app", version="1.0.0", description="Test app")

    @app.device(name=lambda settings: ["sensor-a", "sensor-b"])
    async def dynamic_sensor_handler(ctx: DeviceContext) -> None:
        pass

    return app


def _make_device_callable_app() -> tuple[App, str]:
    app = App(name="dynamic-app", version="1.0.0", description="Test app")

    @app.device(name=lambda s: ["sensor-a", "sensor-b"])
    async def dynamic_device_handler(ctx: DeviceContext) -> None:
        pass

    return app, "dynamic_device_handler"


def _make_telemetry_callable_app() -> tuple[App, str]:
    app = App(name="dynamic-app", version="1.0.0", description="Test app")

    @app.telemetry(name=lambda s: ["telem-a", "telem-b"], interval=5.0)
    async def dynamic_telemetry_handler(ctx: DeviceContext) -> dict[str, object]:
        return {}

    return app, "dynamic_telemetry_handler"


def _make_command_callable_app() -> tuple[App, str]:
    app = App(name="dynamic-app", version="1.0.0", description="Test app")

    @app.command(name=lambda s: ["cmd-a", "cmd-b"])
    async def dynamic_command_handler(ctx: DeviceContext) -> dict[str, object]:
        return {}

    return app, "dynamic_command_handler"


class TestCallableNameGuardRegistrationKinds:
    """Equivalence Partitioning: guard fires for all three registration kinds.

    Test Techniques Used:
        - Equivalence Partitioning: device / telemetry / command are distinct
          registration paths that each contribute to the guard's chain; each
          is an independent equivalence class.
        - Error Guessing: the guard could silently skip a kind if the itertools
          chain only covered devices; this class confirms all three slots.
    """

    @pytest.mark.parametrize(
        ("app_factory",),
        [
            pytest.param(_make_device_callable_app, id="device"),
            pytest.param(_make_telemetry_callable_app, id="telemetry"),
            pytest.param(_make_command_callable_app, id="command"),
        ],
    )
    def test_dump_rejects_callable_name_by_kind(
        self,
        runner: CliRunner,
        app_factory: Callable[[], tuple[App, str]],
    ) -> None:
        """dump exits EXIT_CONFIG_ERROR for each registration kind with callable name=.

        Test Boundary: Guard fires regardless of which registration kind carries the
        callable name=; telemetry and command paths are covered here since the existing
        fixture only exercises the device path.
        """
        app, expected_qualname = app_factory()
        with patch("cosalette._schema._cli._import_app", return_value=app):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "settings-derived" in result.stderr
        assert "ADR-023" in result.stderr
        assert expected_qualname in result.stderr


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
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
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
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
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
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
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

    def test_dump_rejects_callable_name_spec(
        self, runner: CliRunner, callable_name_app: App
    ) -> None:
        """Should exit 1 when any registration carries an unexpanded callable name=.

        Test Boundary: Pre-asyncapi guard for ADR-023 NameSpec registrations.
        Test Technique: Error condition testing — callable name= is not expanded
        at import time so the guard must fire before app.asyncapi() is called.
        """
        with patch(
            "cosalette._schema._cli._import_app", return_value=callable_name_app
        ):
            result = runner.invoke(schema_app, ["dump", "--app", "dummy:app"])

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "settings-derived" in result.stderr
        assert "ADR-023" in result.stderr
        assert "dynamic_sensor_handler" in result.stderr


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
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
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
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
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
        with patch("cosalette._schema._cli._import_app", return_value=mixed_app):
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

    def test_init_rejects_callable_name_spec(
        self, runner: CliRunner, callable_name_app: App
    ) -> None:
        """Should exit 1 when any registration carries an unexpanded callable name=.

        Test Boundary: Pre-asyncapi guard for ADR-023 NameSpec registrations.
        Test Technique: Error condition testing — same guard as dump, exercised
        for init to confirm both commands are covered.
        """
        with patch(
            "cosalette._schema._cli._import_app", return_value=callable_name_app
        ):
            result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "settings-derived" in result.stderr
        assert "ADR-023" in result.stderr
        assert "dynamic_sensor_handler" in result.stderr


class TestConsumerMetadataDocsQuality:
    """Docs-quality guarantees for consumer metadata in generated schemas.

    Regression coverage for the readability of the published (zensical) docs
    artifact: unicode consumer values must be emitted literally (not escaped)
    across every YAML-emitting command (init, dump, slice, ha-discovery), and
    consumer() key order must survive regeneration (cos-1pfl).
    """

    def test_init_emits_unicode_consumer_values_unescaped(
        self, runner: CliRunner, unicode_consumer_app: App
    ) -> None:
        """Should emit '°C' / 'Bq/m³' literally rather than '\\xB0C'.

        Test Boundary: YAML emission of non-ASCII consumer metadata.
        Test Technique: Specification-based testing of allow_unicode output.
        """
        # Arrange / Act
        with patch(
            "cosalette._schema._cli._import_app", return_value=unicode_consumer_app
        ):
            result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])

        # Assert
        assert result.exit_code == EXIT_OK
        output = result.stdout
        assert "unit: °C" in output
        assert "unit: Bq/m³" in output
        # The escaped forms must not leak into the docs artifact.
        assert "\\xB0" not in output
        assert "\\xB3" not in output

    def test_init_preserves_consumer_key_call_order(
        self, runner: CliRunner, unicode_consumer_app: App
    ) -> None:
        """Should keep consumer() call order (unit before device_class).

        Test Boundary: JSON Schema generation ordering of x-cosalette-consumer.
        Test Technique: State-based testing of the order-preserving generator.
        """
        # Arrange / Act
        with patch(
            "cosalette._schema._cli._import_app", return_value=unicode_consumer_app
        ):
            result = runner.invoke(schema_app, ["init", "--app", "dummy:app"])

        # Assert
        assert result.exit_code == EXIT_OK
        output = result.stdout
        # unit is declared before device_class in the consumer() call; without the
        # order-preserving generator pydantic would sort it after device_class.
        # Assert both consumer blocks so a single sampled block doesn't carry the
        # whole regression guarantee.
        assert (
            output.index("unit: °C")
            < output.index("device_class: temperature")
            < output.index("state_class: measurement")
        )
        assert output.index("unit: Bq/m³") < output.index("device_class: radon")

        # Order override is consumer-scoped only: pydantic emits title before
        # required (insertion order); _sort_recursive sorts required (r) < title (t).
        assert output.index("required:") < output.index("title: Reading")

    def test_dump_and_init_agree_on_consumer_metadata(
        self, runner: CliRunner, unicode_consumer_app: App
    ) -> None:
        """Should emit identical x-cosalette-consumer blocks from dump and init.

        Test Boundary: Parity between the dump and init emitters.
        Test Technique: Round-trip testing comparing both command outputs.
        """
        import yaml

        # Arrange
        def _consumer_blocks(argv: list[str]) -> list[dict[str, Any]]:
            with patch(
                "cosalette._schema._cli._import_app",
                return_value=unicode_consumer_app,
            ):
                result = runner.invoke(schema_app, argv)
            assert result.exit_code == EXIT_OK

            def _collect(node: Any) -> list[dict[str, Any]]:
                if not isinstance(node, dict):
                    return []
                found: list[dict[str, Any]] = []
                for k, v in node.items():
                    if k == "x-cosalette-consumer" and isinstance(v, dict):
                        found.append(v)
                    else:
                        found.extend(_collect(v))
                return found

            return _collect(yaml.safe_load(result.stdout))

        # Act
        init_blocks = _consumer_blocks(["init", "--app", "dummy:app"])
        dump_blocks = _consumer_blocks(["dump", "--app", "dummy:app"])

        # Assert — same blocks, same order, and unicode preserved.
        assert init_blocks
        assert init_blocks == dump_blocks
        assert any(b.get("unit") == "°C" for b in init_blocks)

    def test_slice_emits_unicode_consumer_values_unescaped(
        self, runner: CliRunner, network_schema: Path
    ) -> None:
        """Should keep 'unit: °C' literal when slicing an app from a network schema.

        Test Boundary: YAML emission of the slice command (shared _dump_yaml path).
        Test Technique: Specification-based testing of allow_unicode output.
        """
        # Arrange / Act
        result = runner.invoke(
            schema_app,
            ["slice", "--network", str(network_schema), "--app", "vito2mqtt"],
        )

        # Assert
        assert result.exit_code == EXIT_OK
        assert "unit: °C" in result.stdout
        assert "\\xB0" not in result.stdout

    def test_ha_discovery_yaml_emits_unicode_unescaped(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """Should keep '°C' literal in ha-discovery YAML output.

        Test Boundary: YAML emission of the ha-discovery command (shared path).
        Test Technique: Specification-based testing of allow_unicode output.
        """
        # Arrange / Act
        result = runner.invoke(
            schema_app,
            ["ha-discovery", str(consumer_schema), "--format", "yaml"],
        )

        # Assert
        assert result.exit_code == EXIT_OK
        assert "°C" in result.stdout
        assert "\\xB0" not in result.stdout


def test_pydantic_private_sort_recursive_exists() -> None:
    """Sentinel: fires if pydantic removes _sort_recursive before pin is reviewed."""
    from pydantic.json_schema import GenerateJsonSchema

    assert hasattr(GenerateJsonSchema, "_sort_recursive"), (
        "pydantic renamed/removed _sort_recursive; "
        "review _ConsumerAwareGenerateJsonSchema in _asyncapi.py"
    )


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
        make_app_with_devices: Callable[..., App],
    ) -> None:
        """Should report both missing and extra devices in summary.

        Test Boundary: Combined violation types in check output.
        Test Technique: Boundary value analysis of summary formatting.
        """
        # Create app with one schema device missing and one extra
        test_app = make_app_with_devices("temperature", "extra_sensor")

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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

        with patch("cosalette._schema._cli._import_app", return_value=test_app):
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
