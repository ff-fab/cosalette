"""Guidance tools for the cosalette MCP server.

Provides framework guidance tools that reuse the same curated content
as the CLI help commands.
"""

from __future__ import annotations

from typing import Any


def register_guidance_tools(mcp: Any) -> None:
    """Register guidance tools with the MCP server."""

    @mcp.tool()
    def cosalette_help(topic: str) -> str:
        """Get cosalette framework guidance on a specific topic.

        Args:
            topic: Help topic (telemetry, testing, configuration,
                   architecture, react, …)

        Returns:
            Curated help content for the specified topic
        """
        from cosalette._ai_content import AVAILABLE_TOPICS, get_help_content

        try:
            return get_help_content(topic)
        except ValueError as e:
            available = ", ".join(AVAILABLE_TOPICS)
            return f"❌ {e}\n\nAvailable topics: {available}"

    @mcp.tool()
    def cosalette_prime() -> str:
        """Get the cosalette framework bootstrap overview for starting development.

        Returns:
            Comprehensive bootstrap guide with framework patterns and commands
        """
        from cosalette._ai_content import get_prime_content

        return get_prime_content()

    @mcp.tool()
    def cosalette_conventions() -> str:
        """Get the compact cosalette conventions and constraints summary.

        Returns:
            Framework instruction file content with patterns and best practices
        """
        from cosalette._ai_content import get_conventions_content

        return get_conventions_content()
