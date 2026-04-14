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


def register_adr_tools(mcp: Any) -> None:
    """Register F5 ADR tools with the MCP server."""

    @mcp.tool()
    def cosalette_list_adrs() -> str:
        """List all cosalette Architecture Decision Records with status and summary.

        Returns:
            Formatted overview of all ADRs with metadata and summaries
        """
        adrs = _load_adr_index()

        if not adrs:
            return "❌ No ADRs found. ADR index may not be available."

        result = ["📋 cosalette Architecture Decision Records\n"]

        for adr in adrs:
            status_emoji = "✅" if adr.get("status") == "Accepted" else "⏳"
            impact = adr.get("impact", "unknown")
            impact_emoji = {"high": "🔴", "moderate": "🟡", "low": "🟢"}.get(
                impact, "⚫"
            )

            result.append(
                f"{status_emoji} **{adr['id']}**: {adr['title']}\n"
                f"   Status: {adr.get('status', 'Unknown')} | "
                f"Date: {adr.get('date', 'Unknown')} | "
                f"Impact: {impact_emoji} {impact}\n"
                f"   {adr.get('summary', 'No summary available.')}\n"
            )

        return "\n".join(result)

    @mcp.tool()
    def cosalette_get_adr(adr_id: str) -> str:
        """Get the full content of a specific cosalette ADR by ID.

        Args:
            adr_id: ADR identifier (e.g., 'ADR-001', 'ADR-035')

        Returns:
            Complete ADR markdown content or error message
        """
        adrs = _load_adr_index()

        # Normalize ID format (handle both "ADR-001" and "001")
        if not adr_id.startswith("ADR-"):
            adr_id = f"ADR-{adr_id}"

        # Find the ADR
        for adr in adrs:
            if adr.get("id") == adr_id:
                return str(adr.get("content", "❌ ADR content not available."))

        # Not found
        available_ids = [adr.get("id", "unknown") for adr in adrs]
        return (
            f"❌ ADR '{adr_id}' not found.\n\n"
            f"Available ADRs: {', '.join(available_ids)}"
        )

    @mcp.tool()
    def cosalette_search_adrs(query: str) -> str:
        """Search cosalette ADRs by keyword.

        Args:
            query: Search keyword or phrase to match against titles, tags, and content

        Returns:
            Matching ADR summaries with relevance context
        """
        adrs = _load_adr_index()

        if not adrs:
            return "❌ No ADRs found. ADR index may not be available."

        query_lower = query.lower()
        matches = []

        for adr in adrs:
            relevance_reasons = []

            # Check title
            if query_lower in adr.get("title", "").lower():
                relevance_reasons.append("title")

            # Check tags
            tags = adr.get("tags", [])
            if any(query_lower in tag.lower() for tag in tags):
                relevance_reasons.append("tags")

            # Check summary and content (limit content search to avoid huge matches)
            summary = adr.get("summary", "")
            content_preview = adr.get("content", "")[:1000]  # First 1000 chars

            if query_lower in summary.lower():
                relevance_reasons.append("summary")
            elif query_lower in content_preview.lower():
                relevance_reasons.append("content")

            if relevance_reasons:
                match_context = ", ".join(relevance_reasons)
                status_emoji = "✅" if adr.get("status") == "Accepted" else "⏳"

                matches.append(
                    f"{status_emoji} **{adr['id']}**: {adr['title']}\n"
                    f"   Match: {match_context} | "
                    f"Status: {adr.get('status', 'Unknown')} | "
                    f"Date: {adr.get('date', 'Unknown')}\n"
                    f"   {summary}\n"
                )

        if not matches:
            return f"❌ No ADRs found matching '{query}'."

        result = [f"🔍 ADRs matching '{query}' ({len(matches)} results)\n"] + matches

        return "\n".join(result)
