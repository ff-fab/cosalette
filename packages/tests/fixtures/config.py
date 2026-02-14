"""Configuration test fixtures and factories.

Provides fixtures for testing config.py:
- Settings LRU cache management
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from cosalette.config import get_settings


@pytest.fixture
def _reset_settings_cache() -> Iterator[None]:
    """Clear Settings LRU cache before and after test.

    The get_settings() function is cached with @lru_cache. Tests that modify
    environment variables or config files need a fresh Settings instance.

    Usage:
        def test_env_override(_reset_settings_cache, monkeypatch):
            monkeypatch.setenv("OPENHAB_URL", "http://custom:8080")
            settings = get_settings()
            assert settings.openhab_url == "http://custom:8080"
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
