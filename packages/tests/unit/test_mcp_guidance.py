"""Tests for the MCP guidance tools.

Test Techniques Used:
- Equivalence Partitioning: valid/invalid topic names
- Specification-based Testing: tool registration and return formats
- Error Guessing: missing content, unknown topics
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from cosalette._ai_content import AVAILABLE_TOPICS

# Skip all tests if fastmcp is not available
FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None

if FASTMCP_AVAILABLE:
    from cosalette._mcp._guidance import register_guidance_tools


def _call_tool(mcp, name, args=None):
    """Call an MCP tool synchronously and return the text result."""
    result = asyncio.run(mcp.call_tool(name, args or {}))
    return result.content[0].text


def _list_tool_names(mcp):
    """List registered tool names synchronously."""
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestGuidanceToolsRegistration:
    """Tests for MCP guidance tool registration."""

    def test_register_guidance_tools_creates_expected_tools(self):
        """Test that guidance tool registration creates expected MCP tools."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_guidance_tools(mcp)

        # Check that the expected tools are registered
        tool_names = _list_tool_names(mcp)
        expected_tools = {"cosalette_help", "cosalette_prime", "cosalette_conventions"}

        assert expected_tools.issubset(tool_names)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestCosaletteHelpTool:
    """Tests for cosalette_help MCP tool."""

    def setup_method(self):
        """Set up test MCP server with guidance tools."""
        from fastmcp import FastMCP

        self.mcp = FastMCP("test-server")
        register_guidance_tools(self.mcp)

        # Verify the cosalette_help tool is registered
        tool_names = _list_tool_names(self.mcp)
        assert "cosalette_help" in tool_names

    @pytest.mark.parametrize("topic", AVAILABLE_TOPICS)
    def test_cosalette_help_valid_topics(self, topic):
        """Test cosalette_help with all valid topics."""
        result = _call_tool(self.mcp, "cosalette_help", {"topic": topic})

        assert isinstance(result, str)
        assert len(result) > 100  # Should return substantial content
        assert "❌" not in result  # Should not be an error

    def test_cosalette_help_invalid_topic(self):
        """Test cosalette_help with invalid topic returns error."""
        result = _call_tool(self.mcp, "cosalette_help", {"topic": "invalid_topic"})

        assert isinstance(result, str)
        assert "❌" in result
        assert "invalid_topic" in result
        assert "Available topics:" in result

        # Should list available topics
        for topic in AVAILABLE_TOPICS:
            assert topic in result

    def test_cosalette_help_architecture_contains_patterns(self):
        """Test that architecture help contains expected architectural patterns."""
        result = _call_tool(self.mcp, "cosalette_help", {"topic": "architecture"})

        expected_patterns = [
            "Hexagonal Architecture",
            "Dependency Injection",
            "Composition Root",
        ]

        for pattern in expected_patterns:
            assert pattern in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestCosalettePrimeTool:
    """Tests for cosalette_prime MCP tool."""

    def setup_method(self):
        """Set up test MCP server with guidance tools."""
        from fastmcp import FastMCP

        self.mcp = FastMCP("test-server")
        register_guidance_tools(self.mcp)

        # Verify the cosalette_prime tool is registered
        tool_names = _list_tool_names(self.mcp)
        assert "cosalette_prime" in tool_names

    def test_cosalette_prime_returns_bootstrap_content(self):
        """Test that cosalette_prime returns bootstrap overview."""
        result = _call_tool(self.mcp, "cosalette_prime")

        assert isinstance(result, str)
        assert len(result) > 200  # Should be substantial
        assert "cosalette" in result.lower()
        assert "framework" in result.lower()

        # Should include key bootstrap sections
        expected_sections = [
            "Essential Commands",
            "Framework Patterns",
            "Deep Dive Topics",
        ]

        for section in expected_sections:
            assert section in result

    def test_cosalette_prime_includes_version(self):
        """Test that prime content includes version info."""
        result = _call_tool(self.mcp, "cosalette_prime")

        # Should include version pattern
        assert " v" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestCosaletteConventionsTool:
    """Tests for cosalette_conventions MCP tool."""

    def setup_method(self):
        """Set up test MCP server with guidance tools."""
        from fastmcp import FastMCP

        self.mcp = FastMCP("test-server")
        register_guidance_tools(self.mcp)

        # Verify the cosalette_conventions tool is registered
        tool_names = _list_tool_names(self.mcp)
        assert "cosalette_conventions" in tool_names

    def test_cosalette_conventions_returns_instruction_content(self):
        """Test that cosalette_conventions returns instruction file content."""
        result = _call_tool(self.mcp, "cosalette_conventions")

        assert isinstance(result, str)
        assert result  # Non-empty

        # In CI/build environments the instruction file may not be present;
        # accept either the full content or the graceful fallback message.
        if "not found" in result:
            assert "cosalette ai init" in result
        else:
            expected_patterns = ["cosalette", "@app.telemetry", "DeviceContext"]
            for pattern in expected_patterns:
                assert pattern in result

    def test_cosalette_conventions_handles_missing_file(self, monkeypatch):
        """Test graceful handling when instruction file is missing."""

        # Mock the conventions content to simulate missing file
        def mock_get_conventions_content():
            return (
                "cosalette framework instructions not found. "
                "Run 'cosalette ai init' to install the instruction file."
            )

        monkeypatch.setattr(
            "cosalette._ai_content.get_conventions_content",
            mock_get_conventions_content,
        )

        result = _call_tool(self.mcp, "cosalette_conventions")

        assert isinstance(result, str)
        assert "not found" in result
        assert "cosalette ai init" in result
