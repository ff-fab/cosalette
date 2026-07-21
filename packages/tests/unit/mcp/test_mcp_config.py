"""Tests for the MCP configuration tools.

Test Techniques Used:
- Specification-based Testing: schema generation and env var formatting
- Equivalence Partitioning: base settings vs custom specs, empty vs populated
- Error Guessing: import failures, non-BaseSettings classes, malformed specs
"""

from __future__ import annotations

import asyncio
import importlib.util
from unittest.mock import patch

import pytest

# Skip all tests if fastmcp is not available
FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None

if FASTMCP_AVAILABLE:
    from cosalette._mcp._config import register_config_tools


def _call_tool(mcp, name, args=None):
    """Call an MCP tool synchronously and return the text result."""
    result = asyncio.run(mcp.call_tool(name, args or {}))
    return result.content[0].text


def _list_tool_names(mcp):
    """List registered tool names synchronously."""
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Clear the schema cache between tests."""
    if FASTMCP_AVAILABLE:
        import cosalette._mcp._config

        cosalette._mcp._config._schema_cache.clear()


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestConfigToolRegistration:
    """Tests for MCP config tool registration."""

    def test_register_config_tools_creates_expected_tools(self):
        """Test that config tool registration creates expected MCP tools."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Check that the expected tools are registered
        tool_names = _list_tool_names(mcp)
        expected_tools = {"cosalette_config_schema", "cosalette_config_env_vars"}

        assert expected_tools.issubset(tool_names)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestConfigSchema:
    """Tests for cosalette_config_schema tool."""

    def test_schema_with_default_settings(self):
        """Test getting schema with default cosalette Settings."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        result = _call_tool(mcp, "cosalette_config_schema", {"settings_spec": ""})

        # Should return valid JSON
        import json

        data = json.loads(result)

        # Should be a JSON Schema
        assert "properties" in data
        assert isinstance(data["properties"], dict)

        # Should contain expected cosalette settings
        props = data["properties"]
        assert "mqtt" in props
        assert "logging" in props
        assert "schema" in props

    def test_schema_with_empty_string(self):
        """Test getting schema with empty string (same as default)."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        result = _call_tool(mcp, "cosalette_config_schema", {})

        # Should return valid JSON
        import json

        data = json.loads(result)

        # Should be a JSON Schema
        assert "properties" in data

    def test_schema_with_custom_settings(self):
        """Test getting schema with custom settings class."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Mock a custom settings class
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class MockSettings(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="MYAPP_")
            debug: bool = False
            name: str = "default"

        # Mock the import to return our custom settings
        with patch("cosalette._mcp._config._import_settings") as mock_import:
            mock_import.return_value = (MockSettings, None)

            result = _call_tool(
                mcp,
                "cosalette_config_schema",
                {"settings_spec": "myapp.settings:MySettings"},
            )

            # Should return valid JSON
            import json

            data = json.loads(result)

            # Should contain our custom fields
            props = data["properties"]
            assert "debug" in props
            assert "name" in props

    def test_schema_redacts_secret_field_default(self):
        """Hard-coded defaults on secret-looking fields are redacted.

        Regression for MCP-02: the raw schema tool claimed redaction but
        returned model_json_schema() verbatim, leaking a developer's
        hard-coded secret defaults into LLM context.

        Technique: Error Guessing — secret leakage via schema defaults.
        """
        from fastmcp import FastMCP
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class MockSettings(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="MYAPP_")
            api_key: str = "sk-should-not-leak"
            name: str = "default"

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        with patch("cosalette._mcp._config._import_settings") as mock_import:
            mock_import.return_value = (MockSettings, None)
            result = _call_tool(
                mcp,
                "cosalette_config_schema",
                {"settings_spec": "myapp.settings:MySettings"},
            )

        import json

        assert "sk-should-not-leak" not in result
        data = json.loads(result)
        assert data["properties"]["api_key"]["default"] == "<redacted>"
        # Non-secret defaults are preserved.
        assert data["properties"]["name"]["default"] == "default"


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestConfigEnvVars:
    """Tests for cosalette_config_env_vars tool."""

    def test_env_vars_with_default_settings(self):
        """Test getting env vars with default cosalette Settings."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        result = _call_tool(mcp, "cosalette_config_env_vars", {})

        # Should be a string containing environment variables
        assert isinstance(result, str)
        assert "Environment variables:" in result

        # Should contain expected MQTT variables
        assert "MQTT__HOST" in result
        assert "MQTT__PORT" in result

        # Should contain logging variables
        assert "LOGGING__LEVEL" in result

    def test_env_vars_with_custom_settings(self):
        """Test getting env vars with custom settings class that has prefix."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Mock a custom settings class with prefix
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class MockSettings(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="MYAPP_")
            debug: bool = False
            name: str = "default"

        # Mock the import to return our custom settings
        with patch("cosalette._mcp._config._import_settings") as mock_import:
            mock_import.return_value = (MockSettings, None)

            result = _call_tool(
                mcp,
                "cosalette_config_env_vars",
                {"settings_spec": "myapp.settings:MySettings"},
            )

            # Should contain prefixed variables
            assert "MYAPP_DEBUG" in result
            assert "MYAPP_NAME" in result

    def test_env_vars_no_variables(self):
        """Test env vars generation when no variables found."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Mock a settings class with no usable schema
        with patch("cosalette._mcp._config._import_settings") as mock_import:
            mock_class = type("MockSettings", (), {})
            mock_class.model_json_schema = lambda: {"properties": {}}  # ty: ignore[unresolved-attribute]
            mock_import.return_value = (mock_class, None)

            result = _call_tool(mcp, "cosalette_config_env_vars", {})

            assert "No environment variables found" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestConfigSchemaWithCustomSettings:
    """Tests for custom settings handling in schema tools."""

    def test_custom_settings_import_success(self):
        """Test successful import of custom settings."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Create a mock custom settings class
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class MySettings(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="MYAPP_")
            debug: bool = False
            name: str = "default"

        # Mock the import chain
        with patch("importlib.import_module") as mock_import:
            mock_module = type("Module", (), {"MySettings": MySettings})()
            mock_import.return_value = mock_module

            result = _call_tool(
                mcp,
                "cosalette_config_schema",
                {"settings_spec": "myapp.settings:MySettings"},
            )

            # Should succeed
            import json

            data = json.loads(result)
            assert "properties" in data


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestConfigErrors:
    """Tests for error cases in config tools."""

    def test_invalid_settings_spec_format(self):
        """Test error handling for invalid settings spec format."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        result = _call_tool(
            mcp, "cosalette_config_schema", {"settings_spec": "invalid_spec"}
        )

        assert "❌ Invalid" in result
        assert "Expected format: 'module.path:attribute'" in result

    def test_settings_module_not_found(self):
        """Test error handling for module not found."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        result = _call_tool(
            mcp,
            "cosalette_config_schema",
            {"settings_spec": "nonexistent.module:Settings"},
        )

        assert "❌ Could not import module 'nonexistent.module'" in result

    def test_settings_attribute_not_found(self):
        """Test error handling for attribute not found."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Mock import to succeed but not have the attribute
        with patch("importlib.import_module") as mock_import:
            mock_module = type("Module", (), {})()
            mock_import.return_value = mock_module

            result = _call_tool(
                mcp,
                "cosalette_config_schema",
                {"settings_spec": "test.module:NonexistentSettings"},
            )

            assert (
                "❌ Module 'test.module' has no attribute 'NonexistentSettings'"
                in result
            )

    def test_settings_not_a_basesettings_class(self):
        """Test error handling when attribute is not a BaseSettings class."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Mock import to return a non-BaseSettings object
        with patch("importlib.import_module") as mock_import:
            mock_module = type("Module", (), {"settings": "not_a_class"})()
            mock_import.return_value = mock_module

            result = _call_tool(
                mcp,
                "cosalette_config_schema",
                {"settings_spec": "test.module:settings"},
            )

            assert "❌ 'test.module:settings' is not a BaseSettings class" in result

    def test_schema_generation_error(self):
        """Test error handling when schema generation fails."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Mock a settings class that raises an exception during schema generation
        with patch("cosalette._mcp._config._import_settings") as mock_import:
            mock_class = type("MockSettings", (), {})
            mock_class.model_json_schema = lambda: (_ for _ in ()).throw(  # ty: ignore[unresolved-attribute]
                Exception("Schema error")
            )
            mock_import.return_value = (mock_class, None)

            result = _call_tool(mcp, "cosalette_config_schema", {})

            assert "❌ Error generating schema" in result

    def test_env_vars_generation_error(self):
        """Test error handling when env vars generation fails."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_config_tools(mcp)

        # Mock a settings class that raises an exception during schema generation
        with patch("cosalette._mcp._config._import_settings") as mock_import:
            mock_class = type("MockSettings", (), {})
            mock_class.model_json_schema = lambda: (_ for _ in ()).throw(  # ty: ignore[unresolved-attribute]
                Exception("Schema error")
            )
            mock_import.return_value = (mock_class, None)

            result = _call_tool(mcp, "cosalette_config_env_vars", {})

            assert "❌ Error generating environment variables" in result
