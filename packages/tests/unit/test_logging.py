"""Unit tests for cosalette._logging — JSON formatter and config.

Test Techniques Used:
    - Specification-based Testing: JsonFormatter output schema
    - State Inspection: Root logger handler/level after configure
    - Fixture Isolation: Save/restore root logger state
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from cosalette._logging import JsonFormatter, configure_logging
from cosalette._settings import LoggingSettings


@pytest.fixture
def _restore_root_logger() -> Iterator[None]:
    """Save and restore root logger handlers and level.

    Ensures tests that call ``configure_logging()`` don't leak
    state across subsequent tests.
    """
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    for h in root.handlers:
        if h not in original_handlers:
            h.close()
    root.handlers = original_handlers
    root.setLevel(original_level)


class TestJsonFormatter:
    """Tests for JsonFormatter output schema.

    Technique: Specification-based Testing — verifying the
    JSON structure emitted by the formatter.
    """

    def _make_record(
        self,
        message: str = "hello",
        level: int = logging.INFO,
    ) -> logging.LogRecord:
        """Create a minimal LogRecord for testing."""
        return logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="test.py",
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )

    def test_output_is_valid_json(self) -> None:
        """Formatted output is parseable JSON."""
        fmt = JsonFormatter(service="svc")
        record = self._make_record()
        result = json.loads(fmt.format(record))
        assert isinstance(result, dict)

    def test_has_required_fields(self) -> None:
        """Output contains all required fields."""
        fmt = JsonFormatter(service="svc")
        record = self._make_record()
        result = json.loads(fmt.format(record))
        required = {
            "timestamp",
            "level",
            "logger",
            "message",
            "service",
        }
        assert required.issubset(result.keys())

    def test_timestamp_is_utc_iso8601(self) -> None:
        """Timestamp is UTC ISO 8601 format."""
        fmt = JsonFormatter(service="svc")
        record = self._make_record()
        result = json.loads(fmt.format(record))
        ts = result["timestamp"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo == UTC

    def test_service_included(self) -> None:
        """Service name appears in output."""
        fmt = JsonFormatter(service="myapp")
        record = self._make_record()
        result = json.loads(fmt.format(record))
        assert result["service"] == "myapp"

    def test_version_included_when_set(self) -> None:
        """Version appears when non-empty."""
        fmt = JsonFormatter(service="svc", version="1.2.3")
        record = self._make_record()
        result = json.loads(fmt.format(record))
        assert result["version"] == "1.2.3"

    def test_version_omitted_when_empty(self) -> None:
        """Version key is absent when empty string."""
        fmt = JsonFormatter(service="svc", version="")
        record = self._make_record()
        result = json.loads(fmt.format(record))
        assert "version" not in result

    def test_exception_included_when_present(
        self,
    ) -> None:
        """Exception traceback included when logged."""
        fmt = JsonFormatter(service="svc")
        try:
            raise ValueError("boom")
        except ValueError:
            record = self._make_record()
            record.exc_info = (
                ValueError,
                ValueError("boom"),
                None,
            )
        result = json.loads(fmt.format(record))
        assert "exception" in result
        assert "ValueError" in result["exception"]

    def test_stack_info_included_when_present(
        self,
    ) -> None:
        """stack_info included when set on record."""
        fmt = JsonFormatter(service="svc")
        record = self._make_record()
        record.stack_info = "Stack trace here"
        result = json.loads(fmt.format(record))
        assert "stack_info" in result
        assert "Stack trace" in result["stack_info"]


class TestConfigureLogging:
    """Tests for configure_logging() root logger setup.

    Technique: State Inspection — examining root logger
    state after configuration.
    """

    @pytest.mark.usefixtures("_restore_root_logger")
    def test_json_mode_sets_json_formatter(self) -> None:
        """JSON format installs JsonFormatter on handler."""
        settings = LoggingSettings(format="json")
        configure_logging(settings, service="test")

        root = logging.getLogger()
        assert len(root.handlers) >= 1
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)

    @pytest.mark.usefixtures("_restore_root_logger")
    def test_text_mode_sets_standard_formatter(
        self,
    ) -> None:
        """Text format installs standard Formatter."""
        settings = LoggingSettings(format="text")
        configure_logging(settings, service="test")

        root = logging.getLogger()
        assert len(root.handlers) >= 1
        handler = root.handlers[0]
        assert isinstance(handler.formatter, logging.Formatter)
        assert not isinstance(handler.formatter, JsonFormatter)

    @pytest.mark.usefixtures("_restore_root_logger")
    def test_sets_root_logger_level(self) -> None:
        """Root logger level matches settings.level."""
        settings = LoggingSettings(level="WARNING")
        configure_logging(settings, service="test")

        root = logging.getLogger()
        assert root.level == logging.WARNING

    @pytest.mark.usefixtures("_restore_root_logger")
    def test_clears_existing_handlers(self) -> None:
        """Existing handlers are removed before adding."""
        root = logging.getLogger()
        dummy = logging.StreamHandler()
        root.addHandler(dummy)
        initial_count = len(root.handlers)
        assert initial_count >= 1

        settings = LoggingSettings()
        configure_logging(settings, service="test")

        # Only the fresh handler(s) should remain
        for h in root.handlers:
            assert h is not dummy

    @pytest.mark.usefixtures("_restore_root_logger")
    def test_file_handler_added_when_file_set(self, tmp_path: Path) -> None:
        """RotatingFileHandler is added when file is set."""
        settings = LoggingSettings(file=str(tmp_path / "test.log"))
        configure_logging(settings, service="test")

        root = logging.getLogger()
        rotating = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating) == 1
        assert rotating[0].maxBytes == 10 * 1024 * 1024
        assert rotating[0].backupCount == 3
