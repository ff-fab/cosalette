"""Shared AI content for CLI and MCP tools."""

from cosalette._ai_content._help import _get_extra_help, get_help_content
from cosalette._ai_content._meta import (
    AVAILABLE_TOPICS,
    VERSION_FEATURES,
    _get_package_assets_dir,
    get_conventions_content,
    get_prime_content,
    get_version,
    get_whats_new_content,
)

__all__ = [
    "AVAILABLE_TOPICS",
    "VERSION_FEATURES",
    "get_version",
    "_get_package_assets_dir",
    "get_conventions_content",
    "get_prime_content",
    "get_whats_new_content",
    "_get_extra_help",
    "get_help_content",
]
