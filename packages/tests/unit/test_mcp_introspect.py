"""Tests for the MCP introspection tools."""

from __future__ import annotations

import asyncio
import importlib.util
from unittest.mock import patch

import pytest

# Skip all tests if fastmcp is not available
FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None

if FASTMCP_AVAILABLE:
    from cosalette._mcp._introspect import register_introspect_tools


def _call_tool(mcp, name, args=None):
    """Call an MCP tool synchronously and return the text result."""
    result = asyncio.run(mcp.call_tool(name, args or {}))
    return result.content[0].text


def _list_tool_names(mcp):
    """List registered tool names synchronously."""
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestIntrospectToolRegistration:
    """Tests for MCP introspect tool registration."""

    def test_register_introspect_tools_creates_expected_tools(self):
        """Test that introspect tool registration creates expected MCP tools."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Check that the expected tools are registered
        tool_names = _list_tool_names(mcp)
        expected_tools = {
            "cosalette_inspect_app",
            "cosalette_inspect_device",
            "cosalette_inspect_adapters",
        }

        assert expected_tools.issubset(tool_names)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestInspectApp:
    """Tests for cosalette_inspect_app tool."""

    def test_inspect_real_app(self):
        """Test inspecting a real cosalette app."""
        from fastmcp import FastMCP

        import cosalette

        # Create a real app
        app = cosalette.App(name="testapp", version="1.0.0", description="Test app")

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock the import to return our test app
        with patch("cosalette._mcp._introspect._import_app_instance") as mock_import:
            mock_import.return_value = app

            result = _call_tool(
                mcp, "cosalette_inspect_app", {"app_spec": "test.module:app"}
            )

            # Should return valid JSON
            import json

            data = json.loads(result)

            # Check expected structure
            assert "app" in data
            assert data["app"]["name"] == "testapp"
            assert data["app"]["version"] == "1.0.0"
            assert data["app"]["description"] == "Test app"
            assert "devices" in data
            assert "telemetry" in data
            assert "commands" in data
            assert "adapters" in data

    def test_inspect_app_with_device(self):
        """Test inspecting an app with a registered device."""
        from fastmcp import FastMCP

        import cosalette

        # Create app with a device - use a simple handler to avoid injection issues
        app = cosalette.App(name="testapp", version="1.0.0", description="Test app")

        @app.device("test_device")
        async def test_device() -> None:
            """A test device with no parameters."""
            pass

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock the import to return our test app
        with patch("cosalette._mcp._introspect._import_app_instance") as mock_import:
            mock_import.return_value = app

            result = _call_tool(
                mcp, "cosalette_inspect_app", {"app_spec": "test.module:app"}
            )

            # Should return valid JSON
            import json

            data = json.loads(result)

            # Check that devices are present
            assert len(data["devices"]) == 1
            assert data["devices"][0]["name"] == "test_device"
            assert data["devices"][0]["type"] == "device"


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestInspectAppErrors:
    """Tests for error cases in cosalette_inspect_app tool."""

    def test_invalid_spec_format(self):
        """Test error handling for invalid app spec format."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        result = _call_tool(mcp, "cosalette_inspect_app", {"app_spec": "invalid_spec"})
        assert "❌ Invalid app spec" in result
        assert "Expected format: 'module.path:attribute'" in result

    def test_module_not_found(self):
        """Test error handling for module not found."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        result = _call_tool(
            mcp, "cosalette_inspect_app", {"app_spec": "nonexistent.module:app"}
        )
        assert "❌ Could not import module 'nonexistent.module'" in result

    def test_attribute_not_found(self):
        """Test error handling for attribute not found."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock import to succeed but not have the attribute
        with patch("importlib.import_module") as mock_import:
            mock_module = type("Module", (), {})()
            mock_import.return_value = mock_module

            result = _call_tool(
                mcp, "cosalette_inspect_app", {"app_spec": "test.module:nonexistent"}
            )
            assert "❌ Module 'test.module' has no attribute 'nonexistent'" in result

    def test_not_an_app_instance(self):
        """Test error handling when attribute is not an App instance."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock import to return a non-App object
        with patch("importlib.import_module") as mock_import:
            mock_module = type("Module", (), {"app": "not_an_app"})()
            mock_import.return_value = mock_module

            result = _call_tool(
                mcp, "cosalette_inspect_app", {"app_spec": "test.module:app"}
            )
            assert "❌ 'test.module:app' is not an App instance" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestInspectDevice:
    """Tests for cosalette_inspect_device tool."""

    def test_inspect_existing_device(self):
        """Test inspecting an existing device."""
        from fastmcp import FastMCP

        import cosalette

        # Create app with a device - use a simple handler to avoid injection issues
        app = cosalette.App(name="testapp", version="1.0.0", description="Test app")

        @app.device("test_device")
        async def test_device() -> None:
            """A test device with no parameters."""
            pass

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock the import to return our test app
        with patch("cosalette._mcp._introspect._import_app_instance") as mock_import:
            mock_import.return_value = app

            result = _call_tool(
                mcp,
                "cosalette_inspect_device",
                {"app_spec": "test.module:app", "device_name": "test_device"},
            )

            # Should return valid JSON for the device
            import json

            data = json.loads(result)

            assert data["name"] == "test_device"
            assert data["type"] == "device"

    def test_inspect_nonexistent_device(self):
        """Test error handling for nonexistent device."""
        from fastmcp import FastMCP

        import cosalette

        # Create empty app
        app = cosalette.App(name="testapp", version="1.0.0", description="Test app")

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock the import to return our test app
        with patch("cosalette._mcp._introspect._import_app_instance") as mock_import:
            mock_import.return_value = app

            result = _call_tool(
                mcp,
                "cosalette_inspect_device",
                {"app_spec": "test.module:app", "device_name": "nonexistent"},
            )

            assert "❌ Device 'nonexistent' not found in app" in result
            assert "Available devices" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestInspectAdapters:
    """Tests for cosalette_inspect_adapters tool."""

    def test_inspect_empty_adapters(self):
        """Test inspecting adapters when there are none."""
        from fastmcp import FastMCP

        import cosalette

        # Create empty app
        app = cosalette.App(name="testapp", version="1.0.0", description="Test app")

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock the import to return our test app
        with patch("cosalette._mcp._introspect._import_app_instance") as mock_import:
            mock_import.return_value = app

            result = _call_tool(
                mcp, "cosalette_inspect_adapters", {"app_spec": "test.module:app"}
            )

            # Should return empty list
            import json

            data = json.loads(result)
            assert data == []

    def test_inspect_adapters_import_error(self):
        """Test error handling when app import fails."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_introspect_tools(mcp)

        # Mock the import to return an error
        with patch("cosalette._mcp._introspect._import_app_instance") as mock_import:
            mock_import.return_value = "❌ Import failed"

            result = _call_tool(
                mcp, "cosalette_inspect_adapters", {"app_spec": "test.module:app"}
            )
            assert "❌ Import failed" in result
