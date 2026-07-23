"""Tests for the static (no-execution) MCP describe tool.

Test Techniques Used:
- Specification-based Testing: AST extraction of statically visible structure
- Error Guessing: proving zero code execution against a side-effecting module
- Equivalence Partitioning: resolvable path vs. unresolvable module name
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from cosalette._mcp._static_describe import (
    _describe_static,
    _resolve_source_path,
    register_static_describe_tools,
)

FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None

_FIXTURE_APP = '''"""Example cosalette app fixture."""

from cosalette import App

app = App(name="demo", version="1.0.0")


@app.telemetry(interval=60)
async def read_temperature(ctx):
    yield {"celsius": 21.5}


@app.command("valve")
async def set_valve(topic, payload):
    ...


class DeviceConfig:
    host: str = "localhost"
    port: int = 1883


app.run()
'''


class TestStaticDescribeExtraction:
    """The tool renders visible structure and always labels itself best-effort."""

    def test_extracts_visible_structure_and_labels_best_effort(self, tmp_path):
        # Arrange
        mod = tmp_path / "demo_app.py"
        mod.write_text(_FIXTURE_APP, encoding="utf-8")

        # Act
        result = _describe_static(str(mod))

        # Assert — labeled as no-execution best-effort
        assert "STATIC ANALYSIS — no code executed" in result
        # Module docstring
        assert "Example cosalette app fixture." in result
        # Top-level construction
        assert "app = App(name='demo', version='1.0.0')" in result
        # Decorated handlers with decorator + literal args
        assert "read_temperature" in result
        assert "app.telemetry(interval=60)" in result
        assert "set_valve" in result
        assert "app.command('valve')" in result
        # Class with annotated literal field
        assert "class DeviceConfig" in result
        assert "host: str = 'localhost'" in result
        # Top-level call
        assert "app.run()" in result


class TestStaticDescribeNoExecution:
    """The defining safety property: the target module is never executed."""

    def test_does_not_execute_module_side_effects(self, tmp_path):
        # Arrange — a module whose IMPORT would create a marker file.
        marker = tmp_path / "SIDE_EFFECT_MARKER"
        side_effecting = tmp_path / "evil.py"
        side_effecting.write_text(
            '"""Module with an import-time side effect."""\n'
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            "\n"
            "@something.device('x')\n"
            "async def handler():\n"
            "    ...\n",
            encoding="utf-8",
        )

        # Act — describe it statically.
        result = _describe_static(str(side_effecting))

        # Assert — the module's top-level code NEVER ran.
        assert not marker.exists(), "static describe executed module-level code"
        # It still parsed and described the visible structure.
        assert "STATIC ANALYSIS" in result
        assert "handler" in result
        assert "something.device('x')" in result

    def test_unresolvable_module_errors_instead_of_importing(self):
        # A dotted module that is not on disk must return a clear error and
        # must NOT fall back to importing (there is no import code path).
        path, err = _resolve_source_path("nonexistent_pkg.sub.module")

        assert path is None
        assert err is not None
        assert "without importing" in err

    def test_syntax_error_is_reported_without_executing(self, tmp_path):
        bad = tmp_path / "broken.py"
        bad.write_text("def (:\n    pass\n", encoding="utf-8")

        result = _describe_static(str(bad))

        assert "syntax error" in result.lower()
        assert "STATIC ANALYSIS" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestStaticDescribeRegistration:
    """The tool is registered on the MCP server."""

    def test_tool_registered(self):
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_static_describe_tools(mcp)

        tools = asyncio.run(mcp.list_tools())
        assert "cosalette_describe_app_static" in {t.name for t in tools}
