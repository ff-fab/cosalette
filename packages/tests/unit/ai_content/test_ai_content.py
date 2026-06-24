"""Tests for the shared AI content module.

Test Techniques Used:
- Specification-based Testing: API contracts for content functions
- Equivalence Partitioning: valid/invalid topics, version retrieval
- Error Guessing: missing asset files, import failures
"""

from __future__ import annotations

import pytest

from cosalette._ai_content import (
    AVAILABLE_TOPICS,
    get_conventions_content,
    get_help_content,
    get_prime_content,
    get_version,
    get_whats_new_content,
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
            "asyncio_mode",
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

    def test_get_help_content_commands_specifics(self):
        """Test commands help contains expected patterns."""
        content = get_help_content("commands")

        expected_patterns = [
            "@app.command",
            "ctx.commands()",
            "Command",
            "Sub-topic",
            "on_command",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_health_specifics(self):
        """Test health help contains expected patterns."""
        content = get_help_content("health")

        expected_patterns = [
            "HealthCheckable",
            "health_check_interval",
            "Auto-restart",
            "availability",
            "health_check",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_multi_device_specifics(self):
        """Test multi-device help contains expected patterns."""
        content = get_help_content("multi-device")

        expected_patterns = [
            "name=callable",
            "dict[str,",
            "per-device",
            "SensorConfig",
            "@app.telemetry",
            "on_configure",
        ]

        for pattern in expected_patterns:
            assert pattern in content, f"Missing pattern: {pattern}"

    def test_get_help_content_scheduling_specifics(self):
        """Test scheduling help contains expected patterns."""
        content = get_help_content("scheduling")

        expected_patterns = [
            "schedule=",
            "sleep_until",
            "cron",
            "interval=",
            "wall-clock",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_resilience_specifics(self):
        """Test resilience help contains expected patterns."""
        content = get_help_content("resilience")

        expected_patterns = [
            "retry=",
            "backoff",
            "CircuitBreaker",
            "ExponentialBackoff",
            "retry_on",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_sub_entities_specifics(self):
        """Test sub-entities help contains expected patterns."""
        content = get_help_content("sub-entities")

        expected_patterns = [
            "ctx.sub_entity",
            "availability",
            "publish_state",
            "on_command",
            "context manager",
        ]

        for pattern in expected_patterns:
            assert pattern in content

    def test_get_help_content_router_specifics(self):
        """Test router help contains expected patterns and omits unsupported API."""
        content = get_help_content("router")

        # Verify correct patterns are present
        expected_patterns = [
            "Router",
            "app.include_router",
            "prefix=",
            "does NOT exist",
            "routers cannot include other routers",
        ]

        for pattern in expected_patterns:
            assert pattern in content, f"Missing expected pattern: {pattern}"

        # Verify incorrect pattern is absent
        assert "router.include_router()" not in content, (
            "API surface incorrectly lists router.include_router() as available"
        )


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

        # Clear lru_cache so monkeypatch takes effect
        from cosalette._ai_content import _get_package_assets_dir

        get_conventions_content.cache_clear()
        _get_package_assets_dir.cache_clear()

        # Mock the assets directory to return a non-existent path
        def mock_get_package_assets_dir():
            from pathlib import Path

            return Path("/nonexistent/path")

        monkeypatch.setattr(
            "cosalette._ai_content._meta._get_package_assets_dir",
            mock_get_package_assets_dir,
        )

        content = get_conventions_content()

        assert isinstance(content, str)
        assert "not found" in content  # Should have error message
        assert "cosalette ai init" in content  # Should suggest solution


class TestGetWhatsNewContent:
    """Tests for upgrade guidance content generation."""

    def test_get_whats_new_content_valid_old_version(self):
        """Test that valid old version returns newer features."""
        content = get_whats_new_content("0.2.1")

        assert isinstance(content, str)
        assert len(content) > 0
        assert "What's New (since 0.2.1)" in content
        assert "0.3.0" in content  # Should include 0.3.0 features
        assert "0.3.1" in content  # Should include 0.3.1 features
        assert "on_configure" in content  # Should include some 0.3.0 features
        assert "MCP server" in content  # Should include some 0.3.1 features

    def test_get_whats_new_content_latest_version_empty(self):
        """Test that latest version returns empty content."""
        content = get_whats_new_content("0.5.0")

        assert content == ""

    def test_get_whats_new_content_invalid_version_empty(self):
        """Test that invalid version returns empty content."""
        content = get_whats_new_content("invalid.version")

        assert content == ""

    def test_get_whats_new_content_future_version_empty(self):
        """Test that future version returns empty content."""
        content = get_whats_new_content("1.0.0")

        assert content == ""

    def test_get_whats_new_content_version_ordering(self):
        """Test that versions are ordered correctly (newest first)."""
        content = get_whats_new_content("0.2.1")

        # Find positions of version headers (use newline suffix to avoid prefix matches
        # e.g. "### 0.3.1" would match inside "### 0.3.10" without the newline).
        # The \n suffix is safe for all headers: each is followed by feature text,
        # never at end-of-string (content ends after the oldest version's features).
        pos_0310 = content.find("### 0.3.10\n")
        pos_033 = content.find("### 0.3.3\n")
        pos_032 = content.find("### 0.3.2\n")
        pos_031 = content.find("### 0.3.1\n")
        pos_030 = content.find("### 0.3.0\n")

        # 0.3.10 should come before 0.3.3 before ... before 0.3.0 (newest first)
        assert pos_0310 != -1
        assert pos_033 != -1
        assert pos_032 != -1
        assert pos_031 != -1
        assert pos_030 != -1
        assert pos_0310 < pos_033 < pos_032 < pos_031 < pos_030

    def test_get_whats_new_content_empty_string_version(self):
        """Test that empty version string returns empty content."""
        content = get_whats_new_content("")

        assert content == ""

    def test_get_whats_new_content_exact_version_match(self):
        """Test that exact version match returns no content."""
        content = get_whats_new_content("0.3.0")

        # Should include 0.3.1, 0.3.2, 0.3.3 features (versions after 0.3.0)
        assert "0.3.1" in content
        assert "0.3.2" in content
        assert "0.3.3" in content
        assert "### 0.3.0" not in content  # Should not include 0.3.0 itself
