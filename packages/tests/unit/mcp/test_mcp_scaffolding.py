"""Tests for the MCP scaffolding tools.

Test Techniques Used:
- Specification-based Testing: tool registration and rendered output
- Equivalence Partitioning: with/without adapter, with/without app_spec
- Smoke Testing: render → compile → ruff check for all templates
- Error Guessing: name collisions, missing ports
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import subprocess
import sys

import pytest

from cosalette._mcp._scaffolding import (
    _scaffold_adapter_impl,
    _scaffold_device_impl,
    _scaffold_multi_device_impl,
    _scaffold_test_impl,
    _to_pascal,
)

# Skip all tests if fastmcp is not available
FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None

if FASTMCP_AVAILABLE:
    from cosalette._mcp._scaffolding import register_scaffolding_tools

pytestmark = pytest.mark.unit


def _call_tool(mcp, name, args=None):
    """Call an MCP tool synchronously and return the text result."""
    result = asyncio.run(mcp.call_tool(name, args or {}))
    return result.content[0].text


def _list_tool_names(mcp):
    """List registered tool names synchronously."""
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


# ---------------------------------------------------------------------------
# Helper: _to_pascal
# ---------------------------------------------------------------------------


class TestToPascal:
    """Tests for the _to_pascal helper."""

    def test_simple(self):
        assert _to_pascal("temperature") == "Temperature"

    def test_snake_case(self):
        assert _to_pascal("gas_meter") == "GasMeter"

    def test_multi_part(self):
        assert _to_pascal("indoor_air_quality") == "IndoorAirQuality"


# ---------------------------------------------------------------------------
# Template rendering — device
# ---------------------------------------------------------------------------


class TestScaffoldDeviceImpl:
    """Tests for _scaffold_device_impl (no MCP dependency)."""

    def test_basic_device(self):
        """Render a device without adapter injection."""
        result = _scaffold_device_impl("temperature")
        assert '@app.telemetry("temperature"' in result
        assert "app = cosalette.App(" in result
        assert "app.run()" in result
        assert "async def temperature" in result
        assert "DeviceContext" in result
        assert "ctx.adapter(" not in result

    def test_interval_pass_through(self):
        """Custom interval value is rendered into the template."""
        result = _scaffold_device_impl("sensor", interval=30.0)
        assert "interval=30.0" in result

    def test_invalid_device_name_rejected(self):
        """Identifier with invalid characters is rejected before rendering."""
        result = _scaffold_device_impl("bad-name")
        assert "❌" in result
        assert "Invalid device_name" in result

    def test_invalid_interval_rejected(self):
        """Non-positive or non-finite interval is rejected before rendering."""
        import math

        for bad in (0.0, -1.0, math.inf, math.nan):
            result = _scaffold_device_impl("sensor", interval=bad)
            assert "❌" in result, f"Expected rejection for interval={bad}"

    def test_invalid_adapter_port_rejected(self):
        """Adapter port with invalid characters is rejected before rendering."""
        result = _scaffold_device_impl("temperature", adapter_port="bad port!")
        assert "❌" in result

    def test_device_with_adapter(self):
        """Render a device with adapter injection."""
        result = _scaffold_device_impl(
            "temperature",
            adapter_port="TemperaturePort",
            adapter_module="myapp.adapters",
        )
        assert "TemperaturePort" in result
        assert "myapp.adapters" in result
        assert "ctx.adapter(TemperaturePort)" in result
        assert '@app.telemetry("temperature"' in result

    def test_collision_warning(self):
        """Warn when device name collides with existing registration."""
        from unittest.mock import patch

        with patch(
            "cosalette._mcp._scaffolding._existing_names",
            return_value={"temperature"},
        ):
            result = _scaffold_device_impl("temperature", app_spec="mod:app")
            assert "⚠️" in result
            assert "already registered" in result

    def test_port_hint_when_ports_available(self):
        """Suggest registered ports when no adapter_port is given."""
        from unittest.mock import patch

        with (
            patch("cosalette._mcp._scaffolding._existing_names", return_value=set()),
            patch(
                "cosalette._mcp._scaffolding._registered_ports",
                return_value=["GasMeterPort"],
            ),
        ):
            result = _scaffold_device_impl("counter", app_spec="mod:app")
            assert "GasMeterPort" in result
            assert "💡" in result

    def test_compiles(self):
        """Rendered device code compiles without syntax errors."""
        code = _scaffold_device_impl("humidity")
        ast.parse(code)

    def test_compiles_with_adapter(self):
        """Rendered device code with adapter compiles without syntax errors."""
        code = _scaffold_device_impl(
            "humidity", adapter_port="HumidityPort", adapter_module="adapters"
        )
        ast.parse(code)


# ---------------------------------------------------------------------------
# Template rendering — multi-device
# ---------------------------------------------------------------------------


class TestScaffoldMultiDeviceImpl:
    """Tests for _scaffold_multi_device_impl (no MCP dependency)."""

    def test_basic_multi_device(self):
        """Render a multi-device module without adapter injection."""
        result = _scaffold_multi_device_impl("sensor")
        assert "name=lambda s:" in result
        assert "SensorConfig" in result
        assert "@dataclass" in result
        assert "@app.telemetry(" in result
        assert "config: SensorConfig" in result
        assert "app.run()" in result

    def test_config_class_derived_from_name(self):
        """Config class name is PascalCase + Config suffix."""
        result = _scaffold_multi_device_impl("gas_meter")
        assert "GasMeterConfig" in result
        assert "config: GasMeterConfig" in result

    def test_interval_pass_through(self):
        """Custom interval value is rendered into the template."""
        result = _scaffold_multi_device_impl("sensor", interval=30.0)
        assert "interval=30.0" in result

    def test_invalid_device_name_rejected(self):
        """Invalid identifier is rejected before rendering."""
        result = _scaffold_multi_device_impl("bad-name")
        assert "❌" in result

    def test_with_adapter(self):
        """Render a multi-device module with adapter injection."""
        result = _scaffold_multi_device_impl(
            "sensor",
            adapter_port="SensorPort",
            adapter_module="myapp.adapters",
        )
        assert "SensorPort" in result
        assert "myapp.adapters" in result
        assert "ctx.adapter(SensorPort)" in result

    def test_compiles(self):
        """Rendered multi-device code compiles without syntax errors."""
        code = _scaffold_multi_device_impl("humidity")
        ast.parse(code)

    def test_compiles_with_adapter(self):
        """Rendered multi-device code with adapter compiles."""
        code = _scaffold_multi_device_impl(
            "humidity",
            adapter_port="HumidityPort",
            adapter_module="adapters",
        )
        ast.parse(code)


# ---------------------------------------------------------------------------
# Template rendering — adapter
# ---------------------------------------------------------------------------


class TestScaffoldAdapterImpl:
    """Tests for _scaffold_adapter_impl (no MCP dependency)."""

    def test_basic_adapter(self):
        """Render a port + adapter pair."""
        result = _scaffold_adapter_impl("TemperaturePort")
        assert "class TemperaturePort(Protocol)" in result
        assert "class TemperatureAdapter" in result
        assert "class FakeTemperature" in result
        assert "@runtime_checkable" in result

    def test_custom_return_type(self):
        """Honour custom return_type and default_value."""
        result = _scaffold_adapter_impl(
            "CounterPort",
            return_type="int",
            default_value="42",
        )
        assert "def read(self) -> int" in result
        assert "return 42" in result

    def test_compiles(self):
        """Rendered adapter code compiles without syntax errors."""
        code = _scaffold_adapter_impl("SensorPort")
        ast.parse(code)

    def test_rejects_control_chars_in_freetext(self):
        """Newline/control chars in free-text fields are rejected (MCP-03).

        Technique: Error Guessing — code injection via unvalidated free text
        interpolated into generated source with Jinja autoescape off.
        """
        injected = "float\n    def evil(self): ..."
        result = _scaffold_adapter_impl("SensorPort", return_type=injected)
        assert result.startswith("❌")
        assert "return_type" in result
        assert "def evil" not in result

    def test_allows_legitimate_freetext(self):
        """Ordinary type annotations and descriptions are not rejected."""
        result = _scaffold_adapter_impl(
            "SensorPort",
            device_description="a BME280 temperature/humidity sensor",
            return_type="float | None",
            default_value="None",
        )
        assert "def read(self) -> float | None" in result
        assert "return None" in result


# ---------------------------------------------------------------------------
# Template rendering — test
# ---------------------------------------------------------------------------


class TestScaffoldTestImpl:
    """Tests for _scaffold_test_impl (no MCP dependency)."""

    def test_basic_test(self):
        """Render a test module without adapter."""
        result = _scaffold_test_impl("temperature", device_module="devices")
        assert "class TestDeviceTemperature" in result
        assert "class TestDeviceTemperatureIntegration" in result
        assert "AppHarness" in result
        assert "from devices import temperature" in result

    def test_test_with_adapter(self):
        """Render a test module with adapter wiring."""
        result = _scaffold_test_impl(
            "temperature",
            device_module="devices",
            adapter_port="TemperaturePort",
            adapter_module="adapters",
        )
        assert "TemperaturePort" in result
        assert "FakeTemperature" in result

    def test_compiles(self):
        """Rendered test code compiles without syntax errors."""
        code = _scaffold_test_impl("humidity", device_module="devices")
        ast.parse(code)

    def test_compiles_with_adapter(self):
        """Rendered test code with adapter compiles without syntax errors."""
        code = _scaffold_test_impl(
            "humidity",
            device_module="devices",
            adapter_port="HumidityPort",
            adapter_module="adapters",
        )
        ast.parse(code)


# ---------------------------------------------------------------------------
# Smoke tests — render → compile → ruff check
# ---------------------------------------------------------------------------


class TestTemplateSmokeTests:
    """Smoke tests: every template variant renders valid, lint-clean Python."""

    _TEMPLATES = [
        ("device (no adapter)", _scaffold_device_impl, {"device_name": "sensor"}),
        (
            "device (with adapter)",
            _scaffold_device_impl,
            {
                "device_name": "sensor",
                "adapter_port": "SensorPort",
                "adapter_module": "adapters",
            },
        ),
        (
            "multi-device (no adapter)",
            _scaffold_multi_device_impl,
            {"device_name": "sensor"},
        ),
        (
            "multi-device (with adapter)",
            _scaffold_multi_device_impl,
            {
                "device_name": "sensor",
                "adapter_port": "SensorPort",
                "adapter_module": "adapters",
            },
        ),
        ("adapter", _scaffold_adapter_impl, {"port_name": "SensorPort"}),
        (
            "test (no adapter)",
            _scaffold_test_impl,
            {"device_name": "sensor", "device_module": "devices"},
        ),
        (
            "test (with adapter)",
            _scaffold_test_impl,
            {
                "device_name": "sensor",
                "device_module": "devices",
                "adapter_port": "SensorPort",
                "adapter_module": "adapters",
            },
        ),
    ]

    @pytest.mark.parametrize(
        ("label", "fn", "kwargs"),
        _TEMPLATES,
        ids=[t[0] for t in _TEMPLATES],
    )
    def test_compiles(self, label, fn, kwargs):
        """Template output parses as valid Python."""
        code = fn(**kwargs)
        ast.parse(code)

    @pytest.mark.parametrize(
        ("label", "fn", "kwargs"),
        _TEMPLATES,
        ids=[t[0] for t in _TEMPLATES],
    )
    def test_ruff_clean(self, label, fn, kwargs, tmp_path):
        """Template output passes ruff check (lint-clean)."""
        code = fn(**kwargs)
        py_file = tmp_path / "generated.py"
        py_file.write_text(code)

        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(py_file), "--select=E,W,F"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff check failed for {label}:\n{result.stdout}\n{result.stderr}"
        )

    def test_executes(self):
        """Generated no-adapter device executes without runtime errors.

        Regression guard: catches NameError-class bugs where a name is only
        available at type-check time (TYPE_CHECKING import) but used at runtime.
        Also verifies that the telemetry handler is actually registered on the
        app, not just defined.
        """
        import cosalette

        code = _scaffold_device_impl(device_name="sensor")
        globs: dict[str, object] = {}
        exec(compile(code, "<generated>", "exec"), globs)  # noqa: S102
        assert callable(globs.get("sensor")), "generated handler must be callable"
        assert globs.get("app") is not None, "generated code must define app"

        app = globs["app"]
        assert isinstance(app, cosalette.App)
        snapshot = cosalette.build_registry_snapshot(app)
        assert any(t["name"] == "sensor" for t in snapshot.get("telemetry", [])), (
            "telemetry handler 'sensor' must be registered on the generated app"
        )


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestScaffoldingToolRegistration:
    """Tests for MCP scaffolding tool registration."""

    def test_register_scaffolding_tools_creates_expected_tools(self):
        """Registration creates the three scaffold tools."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        tool_names = _list_tool_names(mcp)
        expected = {
            "cosalette_scaffold_device",
            "cosalette_scaffold_multi_device",
            "cosalette_scaffold_adapter",
            "cosalette_scaffold_test",
        }
        assert expected.issubset(tool_names)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestScaffoldDeviceTool:
    """Integration tests for cosalette_scaffold_device via MCP."""

    def test_basic_device_via_mcp(self):
        """Call scaffold_device through the MCP tool interface."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        result = _call_tool(
            mcp,
            "cosalette_scaffold_device",
            {"device_name": "pressure"},
        )
        assert "async def pressure" in result
        assert "@app.telemetry" in result

    def test_device_with_adapter_via_mcp(self):
        """Call scaffold_device with adapter params through MCP."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        result = _call_tool(
            mcp,
            "cosalette_scaffold_device",
            {
                "device_name": "pressure",
                "adapter_port": "PressurePort",
                "adapter_module": "myapp.adapters",
            },
        )
        assert "PressurePort" in result
        assert "myapp.adapters" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestScaffoldMultiDeviceTool:
    """Integration tests for cosalette_scaffold_multi_device via MCP."""

    def test_basic_multi_device_via_mcp(self):
        """Call scaffold_multi_device through the MCP tool interface."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        result = _call_tool(
            mcp,
            "cosalette_scaffold_multi_device",
            {"device_name": "pressure"},
        )
        assert "name=lambda s:" in result
        assert "async def pressure" in result
        assert "PressureConfig" in result

    def test_multi_device_with_adapter_via_mcp(self):
        """Call scaffold_multi_device with adapter params through MCP."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        result = _call_tool(
            mcp,
            "cosalette_scaffold_multi_device",
            {
                "device_name": "pressure",
                "adapter_port": "PressurePort",
                "adapter_module": "myapp.adapters",
            },
        )
        assert "PressurePort" in result
        assert "myapp.adapters" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestScaffoldAdapterTool:
    """Integration tests for cosalette_scaffold_adapter via MCP."""

    def test_basic_adapter_via_mcp(self):
        """Call scaffold_adapter through the MCP tool interface."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        result = _call_tool(
            mcp,
            "cosalette_scaffold_adapter",
            {"port_name": "PressurePort"},
        )
        assert "class PressurePort(Protocol)" in result
        assert "class PressureAdapter" in result
        assert "class FakePressure" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestScaffoldTestTool:
    """Integration tests for cosalette_scaffold_test via MCP."""

    def test_basic_test_via_mcp(self):
        """Call scaffold_test through the MCP tool interface."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        result = _call_tool(
            mcp,
            "cosalette_scaffold_test",
            {"device_name": "pressure", "device_module": "devices"},
        )
        assert "TestDevicePressure" in result
        assert "AppHarness" in result

    def test_dry_run_name_injection_rejected(self):
        """dry_run_name is validated — code must not survive into the module.

        Technique: Adversarial Testing (CWE-94) — a prompt-injected agent may
        supply newline-bearing 'identifiers'; the scaffolder must reject them
        like every other identifier input.
        """
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_scaffolding_tools(mcp)

        for payload in (
            'FakeDoor\nimport os; os.system("id")\nEVIL',
            "FakeDoor; raise RuntimeError",
            'x"; import sys; sys.exit(0); "',
        ):
            result = _call_tool(
                mcp,
                "cosalette_scaffold_test",
                {
                    "device_name": "door",
                    "adapter_port": "DoorPort",
                    "dry_run_name": payload,
                },
            )
            # Rejected as invalid identifier — no generated module returned
            assert "❌" in result
            assert "Invalid dry_run_name" in result
            assert "AppHarness" not in result
            assert "@pytest" not in result
