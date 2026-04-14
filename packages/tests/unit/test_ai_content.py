"""Tests for the shared AI content module."""

from __future__ import annotations

import pytest

from cosalette._ai_content import (
    AVAILABLE_TOPICS,
    get_conventions_content,
    get_help_content,
    get_prime_content,
    get_version,
)


class TestGetVersion:
    """Tests for version retrieval."""

    def test_get_version_returns_string(self):
        """Test that get_version returns a string."""
        version = get_version()
        assert isinstance(version, str)
        assert version  # Non-empty

    def test_get_version_fallback_unknown(self, monkeypatch):
        """Test that get_version falls back to 'unknown' on error."""

        # Mock importlib.metadata.version to raise exception
        def mock_version(_name):
            raise Exception("Test error")

        monkeypatch.setattr("importlib.metadata.version", mock_version)

        version = get_version()
        assert version == "unknown"


class TestGetPrimeContent:
    """Tests for prime bootstrap content."""

    def test_get_prime_content_returns_non_empty_string(self):
        """Test that prime content returns substantial guidance."""
        content = get_prime_content()

        assert isinstance(content, str)
        assert len(content) > 100  # Should be substantial content
        assert "cosalette" in content.lower()
        assert "framework" in content.lower()

    def test_prime_content_includes_version(self):
        """Test that prime content includes version information."""
        content = get_prime_content()

        # Should include version pattern (v followed by something)
        assert " v" in content

    def test_prime_content_includes_key_sections(self):
        """Test that prime content includes expected guidance sections."""
        content = get_prime_content()

        expected_sections = [
            "Essential Commands",
            "Framework Patterns",
            "Project Structure",
            "Key Capabilities",
            "Deep Dive Topics",
        ]

        for section in expected_sections:
            assert section in content


class TestGetHelpContent:
    """Tests for topic-specific help content."""

    def test_available_topics_non_empty(self):
        """Test that AVAILABLE_TOPICS contains expected topics."""
        assert len(AVAILABLE_TOPICS) > 0
        assert "telemetry" in AVAILABLE_TOPICS
        assert "testing" in AVAILABLE_TOPICS
        assert "configuration" in AVAILABLE_TOPICS
        assert "architecture" in AVAILABLE_TOPICS

    @pytest.mark.parametrize("topic", AVAILABLE_TOPICS)
    def test_get_help_content_valid_topics(self, topic):
        """Test that all available topics return substantial content."""
        content = get_help_content(topic)

        assert isinstance(content, str)
        assert len(content) > 100  # Should be substantial
        assert "cosalette" in content.lower() or "framework" in content.lower()

    def test_get_help_content_telemetry_specifics(self):
        """Test telemetry help contains expected patterns."""
        content = get_help_content("telemetry")

        expected_patterns = [
            "@app.telemetry",
            "dict[str, object]",
            "interval=",
            "DeviceContext",
            "dependency injection",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_testing_specifics(self):
        """Test testing help contains expected patterns."""
        content = get_help_content("testing")

        expected_patterns = [
            "AppHarness",
            "MockMqttClient",
            "@pytest.mark.asyncio",
            "integration test",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_configuration_specifics(self):
        """Test configuration help contains expected patterns."""
        content = get_help_content("configuration")

        expected_patterns = [
            "cosalette.Settings",
            "pydantic",
            "env_prefix",
            "MyAppSettings",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_architecture_specifics(self):
        """Test architecture help contains expected patterns."""
        content = get_help_content("architecture")

        expected_patterns = [
            "Hexagonal Architecture",
            "Dependency Injection",
            "Composition Root",  # Capital letters to match actual content
            "async",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_invalid_topic_raises_value_error(self):
        """Test that invalid topic raises ValueError with available topics."""
        with pytest.raises(ValueError) as exc_info:
            get_help_content("invalid_topic")

        error_message = str(exc_info.value)
        assert "invalid_topic" in error_message
        assert "telemetry" in error_message
        assert "testing" in error_message

    def test_get_help_content_case_sensitive(self):
        """Test that topic matching is case sensitive."""
        with pytest.raises(ValueError):
            get_help_content("TELEMETRY")  # Should be lowercase

        with pytest.raises(ValueError):
            get_help_content("Testing")  # Should be lowercase


class TestGetConventionsContent:
    """Tests for conventions/instructions content."""

    def test_get_conventions_content_returns_string(self):
        """Test that conventions content returns a string."""
        content = get_conventions_content()

        assert isinstance(content, str)
        assert content  # Non-empty

    def test_conventions_content_includes_framework_patterns(self):
        """Test that conventions content includes key framework patterns."""
        content = get_conventions_content()

        # Should include instruction file content with key patterns
        expected_patterns = [
            "cosalette",
            "framework",
            "@app.telemetry",
            "DeviceContext",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_conventions_content_handles_missing_file_gracefully(self, monkeypatch):
        """Test that missing instruction file is handled gracefully."""

        # Mock the assets directory to return a non-existent path
        def mock_get_package_assets_dir():
            from pathlib import Path

            return Path("/nonexistent/path")

        monkeypatch.setattr(
            "cosalette._ai_content._get_package_assets_dir", mock_get_package_assets_dir
        )

        content = get_conventions_content()

        assert isinstance(content, str)
        assert "not found" in content  # Should have error message
        assert "cosalette ai init" in content  # Should suggest solution
