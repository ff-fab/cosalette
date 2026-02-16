"""Pytest configuration and shared fixtures."""

import pytest

# Register the cosalette testing plugin for auto-registered fixtures
# (mock_mqtt, fake_clock, device_context).  Loaded via conftest rather
# than a pyproject.toml entry-point so that pytest-cov can start
# coverage tracing *before* the cosalette import chain runs.
pytest_plugins = ["cosalette.testing._plugin"]


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "unit: Unit tests (fast, no external dependencies)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (may require external services)"
    )
    pass
