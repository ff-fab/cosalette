"""Tests for the MCP ADR tools.

Test Techniques Used:
- Equivalence Partitioning: valid/invalid ADR IDs, search queries
- State Transition Testing: ADR index cache states (empty, loaded, error)
- Error Guessing: missing index file, malformed JSON or gzip, unknown ADR IDs
"""

from __future__ import annotations

import asyncio
import gzip
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

# Skip all tests if fastmcp is not available
FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None

if FASTMCP_AVAILABLE:
    from cosalette._mcp._adrs import _load_adr_index, register_adr_tools


def _call_tool(mcp, name, args=None):
    """Call an MCP tool synchronously and return the text result."""
    result = asyncio.run(mcp.call_tool(name, args or {}))
    return result.content[0].text


def _list_tool_names(mcp):
    """List registered tool names synchronously."""
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


# Sample ADR data for testing
SAMPLE_ADR_INDEX = [
    {
        "id": "ADR-001",
        "title": "Framework Architecture Style",
        "status": "Accepted",
        "date": "2026-02-14",
        "impact": "high",
        "tags": ["architecture"],
        "summary": (
            "The cosalette project needs to provide common infrastructure "
            "for 8+ IoT-to-MQTT bridge applications."
        ),
        "content": (
            "# ADR-001: Framework Architecture Style\n\n## Context\n\n"
            "The cosalette project needs to provide common infrastructure..."
        ),
    },
    {
        "id": "ADR-002",
        "title": "MQTT Topic Conventions",
        "status": "Accepted",
        "date": "2026-02-14",
        "impact": "moderate",
        "tags": ["mqtt", "naming"],
        "summary": (
            "All 8 IoT-to-MQTT bridge projects need a standardised MQTT topic layout."
        ),
        "content": (
            "# ADR-002: MQTT Topic Conventions\n\n## Context\n\n"
            "All 8 IoT-to-MQTT bridge projects need..."
        ),
    },
    {
        "id": "ADR-035",
        "title": "Optional MCP Layer",
        "status": "Accepted",
        "date": "2026-04-14",
        "impact": "high",
        "tags": ["packaging", "cli", "documentation"],
        "summary": "Add MCP server support for downstream AI integration.",
        "content": (
            "# ADR-035: Optional MCP Layer\n\n## Context\n\nAdd MCP server support..."
        ),
    },
]


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestAdrIndexLoading:
    """Tests for loading the gzipped ADR index."""

    @pytest.fixture
    def index_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[Path]:
        """Point the loader at a temporary index and reset its cache."""
        path = tmp_path / "adr-index.json.gz"
        monkeypatch.setattr("cosalette._mcp._adrs._ADR_INDEX", path)
        _load_adr_index.cache_clear()
        yield path
        _load_adr_index.cache_clear()

    def test_load_adr_index_with_existing_file(self, index_path: Path) -> None:
        """A gzipped index round-trips to the original entries."""
        # Arrange
        index_path.write_bytes(gzip.compress(json.dumps(SAMPLE_ADR_INDEX).encode()))

        # Act
        result = _load_adr_index()

        # Assert
        assert list(result) == SAMPLE_ADR_INDEX
        assert result[0]["id"] == "ADR-001"

    def test_load_adr_index_with_missing_file(self, index_path: Path) -> None:
        """A missing index yields no ADRs instead of raising."""
        assert _load_adr_index() == ()

    def test_load_adr_index_caching(self, index_path: Path) -> None:
        """The index is read once; later calls return the cached tuple."""
        # Arrange
        index_path.write_bytes(gzip.compress(json.dumps(SAMPLE_ADR_INDEX).encode()))
        first = _load_adr_index()
        index_path.unlink()

        # Act
        second = _load_adr_index()

        # Assert
        assert first is second

    @pytest.mark.parametrize(
        "payload",
        [gzip.compress(b"invalid json {"), b"not gzip at all", b""],
        ids=["bad_json", "not_gzip", "empty"],
    )
    def test_load_adr_index_handles_corrupt_file(
        self, index_path: Path, payload: bytes
    ) -> None:
        """Technique: Error Guessing — unreadable index degrades to no ADRs."""
        # Arrange
        index_path.write_bytes(payload)

        # Act / Assert
        assert _load_adr_index() == ()

    def test_packaged_index_is_gzipped_and_loads(self) -> None:
        """Technique: Specification-based — the shipped asset is the .gz index."""
        # Arrange
        from cosalette._mcp import _adrs

        _load_adr_index.cache_clear()

        # Act
        adrs = _load_adr_index()

        # Assert
        assert _adrs._ADR_INDEX.name == "adr-index.json.gz"
        assert not _adrs._ADR_INDEX.with_suffix("").exists()
        assert any(adr["id"] == "ADR-035" for adr in adrs)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestAdrToolsRegistration:
    """Tests for MCP ADR tool registration."""

    def test_register_adr_tools_creates_expected_tools(self):
        """Test that ADR tool registration creates expected MCP tools."""
        from fastmcp import FastMCP

        mcp = FastMCP("test-server")
        register_adr_tools(mcp)

        # Check that the expected tools are registered
        tool_names = _list_tool_names(mcp)
        expected_tools = {
            "cosalette_list_adrs",
            "cosalette_get_adr",
            "cosalette_search_adrs",
        }

        assert expected_tools.issubset(tool_names)


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestCosaletteListAdrs:
    """Tests for cosalette_list_adrs MCP tool."""

    def setup_method(self):
        """Set up test MCP server with ADR tools."""
        from fastmcp import FastMCP

        self.mcp = FastMCP("test-server")
        register_adr_tools(self.mcp)

        # Verify the cosalette_list_adrs tool is registered
        tool_names = _list_tool_names(self.mcp)
        assert "cosalette_list_adrs" in tool_names

    def test_list_adrs_with_available_adrs(self, monkeypatch):
        """Test listing ADRs when ADRs are available."""

        # Mock _load_adr_index to return sample data
        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_list_adrs")

        assert isinstance(result, str)
        assert "📋 cosalette Architecture Decision Records" in result
        assert "ADR-001" in result
        assert "ADR-002" in result
        assert "ADR-035" in result
        assert "Framework Architecture Style" in result
        assert "MQTT Topic Conventions" in result

        # Check status emojis
        assert "✅" in result  # Accepted status

        # Check impact indicators
        assert "🔴" in result  # high impact
        assert "🟡" in result  # moderate impact

    def test_list_adrs_with_no_adrs(self, monkeypatch):
        """Test listing ADRs when no ADRs are available."""

        def mock_load_adr_index():
            return []

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_list_adrs")

        assert isinstance(result, str)
        assert "❌ No ADRs found" in result
        assert "ADR index may not be available" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestCosaletteGetAdr:
    """Tests for cosalette_get_adr MCP tool."""

    def setup_method(self):
        """Set up test MCP server with ADR tools."""
        from fastmcp import FastMCP

        self.mcp = FastMCP("test-server")
        register_adr_tools(self.mcp)

        # Verify the cosalette_get_adr tool is registered
        tool_names = _list_tool_names(self.mcp)
        assert "cosalette_get_adr" in tool_names

    def test_get_adr_existing_id(self, monkeypatch):
        """Test getting ADR by existing ID."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_get_adr", {"adr_id": "ADR-001"})

        assert isinstance(result, str)
        assert "# ADR-001: Framework Architecture Style" in result
        assert "common infrastructure" in result
        assert "❌" not in result  # Should not be an error

    def test_get_adr_id_without_prefix(self, monkeypatch):
        """Test getting ADR by ID without 'ADR-' prefix."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_get_adr", {"adr_id": "002"})

        assert isinstance(result, str)
        assert "# ADR-002: MQTT Topic Conventions" in result
        assert "❌" not in result

    def test_get_adr_nonexistent_id(self, monkeypatch):
        """Test getting ADR by non-existent ID."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_get_adr", {"adr_id": "ADR-999"})

        assert isinstance(result, str)
        assert "❌ ADR 'ADR-999' not found" in result
        assert "Available ADRs:" in result
        assert "ADR-001" in result


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestCosaletteSearchAdrs:
    """Tests for cosalette_search_adrs MCP tool."""

    def setup_method(self):
        """Set up test MCP server with ADR tools."""
        from fastmcp import FastMCP

        self.mcp = FastMCP("test-server")
        register_adr_tools(self.mcp)

        # Verify the cosalette_search_adrs tool is registered
        tool_names = _list_tool_names(self.mcp)
        assert "cosalette_search_adrs" in tool_names

    def test_search_adrs_title_match(self, monkeypatch):
        """Test searching ADRs by title keyword."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(
            self.mcp, "cosalette_search_adrs", {"query": "architecture"}
        )

        assert isinstance(result, str)
        assert "🔍 ADRs matching 'architecture'" in result
        assert "ADR-001" in result
        assert "Framework Architecture Style" in result
        assert "Match: title" in result

    def test_search_adrs_tag_match(self, monkeypatch):
        """Test searching ADRs by tag."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_search_adrs", {"query": "mqtt"})

        assert isinstance(result, str)
        assert "ADR-002" in result
        assert "MQTT Topic Conventions" in result
        assert "tags" in result  # Should match in tags field

    def test_search_adrs_summary_match(self, monkeypatch):
        """Test searching ADRs by summary content."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_search_adrs", {"query": "bridge"})

        assert isinstance(result, str)
        # Should match ADR-001 and ADR-002 which mention "bridge" in summary
        assert "bridge" in result.lower()

    def test_search_adrs_no_match(self, monkeypatch):
        """Test searching ADRs with no matches."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(
            self.mcp, "cosalette_search_adrs", {"query": "nonexistent_keyword"}
        )

        assert isinstance(result, str)
        assert "❌ No ADRs found matching 'nonexistent_keyword'" in result

    def test_search_adrs_case_insensitive(self, monkeypatch):
        """Test that ADR search is case insensitive."""

        def mock_load_adr_index():
            return SAMPLE_ADR_INDEX

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result_upper = _call_tool(self.mcp, "cosalette_search_adrs", {"query": "MQTT"})
        result_lower = _call_tool(self.mcp, "cosalette_search_adrs", {"query": "mqtt"})

        # Both should find the same ADR
        assert "ADR-002" in result_upper
        assert "ADR-002" in result_lower

    def test_search_adrs_no_available_adrs(self, monkeypatch):
        """Test searching when no ADRs are available."""

        def mock_load_adr_index():
            return []

        monkeypatch.setattr("cosalette._mcp._adrs._load_adr_index", mock_load_adr_index)

        result = _call_tool(self.mcp, "cosalette_search_adrs", {"query": "anything"})

        assert isinstance(result, str)
        assert "❌ No ADRs found" in result
        assert "ADR index may not be available" in result
