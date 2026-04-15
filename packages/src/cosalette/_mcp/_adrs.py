"""F5: ADR context tools for the cosalette MCP server.

Provides ADR (Architecture Decision Record) search and retrieval tools
using the packaged ADR index.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any


@functools.lru_cache(maxsize=1)
def _load_adr_index() -> tuple[dict[str, Any], ...]:
    """Load the packaged ADR index (cached after first call).

    Returns a tuple (immutable) so the cached value cannot be mutated.
    """
    try:
        import cosalette

        package_path = Path(cosalette.__file__).parent
        index_file = package_path / "assets" / "guidance" / "adr-index.json"

        if not index_file.exists():
            return ()

        with index_file.open(encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)

        return tuple(data)

    except Exception:
        return ()


def _list_adrs_impl() -> str:
    """Format all ADRs as a readable summary."""
    adrs = _load_adr_index()
    if not adrs:
        return "❌ No ADRs found. ADR index may not be available."

    result = ["📋 cosalette Architecture Decision Records\n"]
    for adr in adrs:
        result.append(_format_adr_summary(adr))
    return "\n".join(result)


def _format_adr_summary(adr: dict[str, Any]) -> str:
    """Render a single ADR as a summary block."""
    status_emoji = "✅" if adr.get("status") == "Accepted" else "⏳"
    impact = adr.get("impact", "unknown")
    impact_emoji = {"high": "🔴", "moderate": "🟡", "low": "🟢"}.get(impact, "⚫")
    return (
        f"{status_emoji} **{adr['id']}**: {adr['title']}\n"
        f"   Status: {adr.get('status', 'Unknown')} | "
        f"Date: {adr.get('date', 'Unknown')} | "
        f"Impact: {impact_emoji} {impact}\n"
        f"   {adr.get('summary', 'No summary available.')}\n"
    )


def _get_adr_impl(adr_id: str) -> str:
    """Retrieve full ADR content by ID."""
    adrs = _load_adr_index()
    if not adr_id.startswith("ADR-"):
        adr_id = f"ADR-{adr_id}"

    for adr in adrs:
        if adr.get("id") == adr_id:
            return str(adr.get("content", "❌ ADR content not available."))

    available_ids = [adr.get("id", "unknown") for adr in adrs]
    return f"❌ ADR '{adr_id}' not found.\n\nAvailable ADRs: {', '.join(available_ids)}"


def _search_adrs_impl(query: str) -> str:
    """Search ADRs by keyword across titles, tags, summaries, and content."""
    adrs = _load_adr_index()
    if not adrs:
        return "❌ No ADRs found. ADR index may not be available."

    query_lower = query.lower()
    matches = [
        _format_search_hit(adr, reasons)
        for adr in adrs
        if (reasons := _match_adr(adr, query_lower))
    ]

    if not matches:
        return f"❌ No ADRs found matching '{query}'."
    return "\n".join(
        [f"🔍 ADRs matching '{query}' ({len(matches)} results)\n", *matches]
    )


def _match_adr(adr: dict[str, Any], query_lower: str) -> list[str]:
    """Return relevance reasons if *adr* matches *query_lower*, else empty list."""
    reasons: list[str] = []
    if query_lower in adr.get("title", "").lower():
        reasons.append("title")
    if any(query_lower in t.lower() for t in adr.get("tags", [])):
        reasons.append("tags")
    summary = adr.get("summary", "")
    if query_lower in summary.lower():
        reasons.append("summary")
    elif query_lower in adr.get("content", "")[:1000].lower():
        reasons.append("content")
    return reasons


def _format_search_hit(adr: dict[str, Any], reasons: list[str]) -> str:
    """Format a single search match."""
    status_emoji = "✅" if adr.get("status") == "Accepted" else "⏳"
    return (
        f"{status_emoji} **{adr['id']}**: {adr['title']}\n"
        f"   Match: {', '.join(reasons)} | "
        f"Status: {adr.get('status', 'Unknown')} | "
        f"Date: {adr.get('date', 'Unknown')}\n"
        f"   {adr.get('summary', '')}\n"
    )


def register_adr_tools(mcp: Any) -> None:
    """Register F5 ADR tools with the MCP server."""

    @mcp.tool()
    def cosalette_list_adrs() -> str:
        """List all cosalette Architecture Decision Records with status and summary.

        Returns:
            Formatted overview of all ADRs with metadata and summaries
        """
        return _list_adrs_impl()

    @mcp.tool()
    def cosalette_get_adr(adr_id: str) -> str:
        """Get the full content of a specific cosalette ADR by ID.

        Args:
            adr_id: ADR identifier (e.g., 'ADR-001', 'ADR-035')

        Returns:
            Complete ADR markdown content or error message
        """
        return _get_adr_impl(adr_id)

    @mcp.tool()
    def cosalette_search_adrs(query: str) -> str:
        """Search cosalette ADRs by keyword.

        Args:
            query: Search keyword or phrase to match against titles, tags, and content

        Returns:
            Matching ADR summaries with relevance context
        """
        return _search_adrs_impl(query)
