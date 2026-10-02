"""Unit tests for cosalette._redact — the ADR-085 redaction hook.

Covers building the redactor from ``App(redact=...)``, the fail-open
behaviour, the logging filter installed by ``configure_logging`` and the
redaction of disclosed error payloads.

Test Techniques Used:
    - Equivalence Partitioning: the accepted ``redact`` forms (None, callable,
      patterns, an existing Redactor) against the rejected ones (bare str,
      bytes, wrong member type, invalid regex).
    - Decision Table: payload disclosure (verbose / disclose set / legacy map
      / undisclosed) x redactor set — the redactor runs only on disclosed text.
    - Error Guessing: a redactor that raises or returns a non-string, logged
      once per process; a single string mistaken for a pattern list.
    - State Inspection: handler filters after ``configure_logging``; the
      original record left untouched for other handlers.
    - Specification-based Testing: JsonFormatter prefers ``exc_text``.

See Also:
    ADR-085 — Optional redaction hook for logs and disclosed error messages.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, override

import pytest

import cosalette._redact as redact_module
from cosalette import App
from cosalette._errors import ErrorPublisher, build_error_payload
from cosalette._logging import JsonFormatter, configure_logging
from cosalette._mqtt import MockMqttClient
from cosalette._redact import (
    REDACTED,
    RedactingFilter,
    Redactor,
    build_redactor,
)
from cosalette._settings import LoggingSettings
from cosalette._wiring._infra import create_services
from cosalette.testing import FakeClock

pytestmark = pytest.mark.unit

REDACTION_SENTINEL = "redaction-sentinel"


class SensorError(Exception):
    """App-owned exception used for disclosure tests."""


@pytest.fixture(autouse=True)
def _reset_failure_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with the process-wide failure warning not yet logged."""
    monkeypatch.setattr(redact_module, "_failure_warned", False)


@pytest.fixture
def _restore_root_logger() -> Iterator[None]:
    """Save and restore the root logger's handlers and level."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    for handler in root.handlers:
        if handler not in original_handlers:
            handler.close()
    root.handlers = original_handlers
    root.setLevel(original_level)


def _token_redactor() -> Redactor:
    redactor = build_redactor([r"token=[^&\s]+"])
    assert redactor is not None
    return redactor


def _record(
    msg: str,
    *args: object,
    exc_info: logging._SysExcInfoType | None = None,
    stack_info: str | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.redact",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )
    record.stack_info = stack_info
    return record


def _exc_info(message: str) -> logging._SysExcInfoType:
    try:
        raise SensorError(message)
    except SensorError:
        return sys.exc_info()


# ---------------------------------------------------------------------------
# build_redactor — accepted and rejected forms
# ---------------------------------------------------------------------------


class TestBuildRedactor:
    """Equivalence partitions of the ``redact`` argument."""

    def test_none_is_off(self) -> None:
        # Act
        result = build_redactor(None)

        # Assert
        assert result is None

    def test_callable_is_used_as_is(self) -> None:
        # Arrange
        redactor = build_redactor(lambda text: text.replace(REDACTION_SENTINEL, "***"))
        assert redactor is not None

        # Act
        result = redactor(f"password {REDACTION_SENTINEL}")

        # Assert
        assert result == "password ***"

    def test_string_patterns_replace_every_match(self) -> None:
        # Arrange
        redactor = build_redactor([r"token=[^&\s]+", r"\d{4}-\d{4}"])
        assert redactor is not None

        # Act
        result = redactor("GET /?token=abc&x=1 token=def card 1234-5678")

        # Assert
        assert result == (f"GET /?{REDACTED}&x=1 {REDACTED} card {REDACTED}")

    def test_compiled_pattern_keeps_its_flags(self) -> None:
        # Arrange
        mac = re.compile(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", re.IGNORECASE)
        redactor = build_redactor([mac])
        assert redactor is not None

        # Act
        result = redactor("device aa:bb:cc:dd:ee:ff offline")

        # Assert
        assert result == f"device {REDACTED} offline"

    def test_patterns_from_a_generator_are_accepted(self) -> None:
        # Arrange — any iterable, consumed once at build time
        redactor = build_redactor(p for p in [REDACTION_SENTINEL])
        assert redactor is not None

        # Act
        first = redactor(REDACTION_SENTINEL)
        second = redactor(REDACTION_SENTINEL)

        # Assert
        assert first == second == REDACTED

    def test_empty_pattern_list_leaves_text_unchanged(self) -> None:
        # Arrange
        redactor = build_redactor([])
        assert redactor is not None

        # Act
        result = redactor(REDACTION_SENTINEL)

        # Assert
        assert result == REDACTION_SENTINEL

    def test_existing_redactor_is_returned_unchanged(self) -> None:
        # Arrange
        redactor = _token_redactor()

        # Act
        result = build_redactor(redactor)

        # Assert
        assert result is redactor

    @pytest.mark.parametrize("spec", ["token=.*", b"token=.*"])
    def test_single_string_is_rejected(self, spec: object) -> None:
        # Act / Assert — a bare str would be one pattern per character
        with pytest.raises(TypeError, match="wrap it in a list"):
            build_redactor(spec)  # ty: ignore[invalid-argument-type]

    @pytest.mark.parametrize("spec", [42, 1.5, object()])
    def test_non_iterable_non_callable_is_rejected(self, spec: object) -> None:
        # Act / Assert
        with pytest.raises(TypeError, match="redact must be None"):
            build_redactor(spec)  # ty: ignore[invalid-argument-type]

    @pytest.mark.parametrize("member", [42, None, b"token"])
    def test_wrong_member_type_is_rejected(self, member: object) -> None:
        # Act / Assert
        with pytest.raises(TypeError, match=r"str or re\.Pattern"):
            build_redactor(["ok", member])  # ty: ignore[invalid-argument-type]

    def test_bytes_pattern_is_rejected(self) -> None:
        # Act / Assert
        with pytest.raises(TypeError, match="text patterns"):
            build_redactor([re.compile(b"token")])  # ty: ignore[invalid-argument-type]

    def test_invalid_regex_raises_value_error(self) -> None:
        # Act / Assert
        with pytest.raises(ValueError, match=r"invalid redact pattern '\('"):
            build_redactor(["("])


# ---------------------------------------------------------------------------
# Fail open
# ---------------------------------------------------------------------------


class TestFailOpen:
    """A broken redactor never breaks logging or publishing."""

    def test_raising_redactor_passes_text_through(self) -> None:
        # Arrange
        def broken(text: str) -> str:
            raise RuntimeError(REDACTION_SENTINEL)

        redactor = build_redactor(broken)
        assert redactor is not None

        # Act
        result = redactor("unchanged")

        # Assert
        assert result == "unchanged"

    def test_non_string_return_passes_text_through(self) -> None:
        # Arrange
        redactor = build_redactor(lambda text: None)  # ty: ignore[invalid-argument-type]
        assert redactor is not None

        # Act
        result = redactor("unchanged")

        # Assert
        assert result == "unchanged"

    def test_failure_is_warned_once_per_process(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — two separate redactors share the one process-wide warning
        def broken(text: str) -> str:
            raise RuntimeError(REDACTION_SENTINEL)

        first = build_redactor(broken)
        second = build_redactor(broken)
        assert first is not None
        assert second is not None
        caplog.set_level(logging.WARNING, logger="cosalette._redact")

        # Act
        first("a")
        first("b")
        second("c")

        # Assert — one line, naming the type but never the exception text
        warnings = [r for r in caplog.records if r.name == "cosalette._redact"]
        assert len(warnings) == 1
        assert "RuntimeError" in warnings[0].getMessage()
        assert REDACTION_SENTINEL not in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# RedactingFilter
# ---------------------------------------------------------------------------


class TestRedactingFilter:
    """The filter redacts a copy of the record."""

    def test_message_with_args_is_redacted(self) -> None:
        # Arrange
        log_filter = RedactingFilter(_token_redactor(), logging.Formatter())
        record = _record("calling %s", "https://x/?token=abc")

        # Act
        result = log_filter.filter(record)

        # Assert
        assert result.getMessage() == f"calling https://x/?{REDACTED}"

    def test_original_record_is_untouched(self) -> None:
        # Arrange
        log_filter = RedactingFilter(_token_redactor(), logging.Formatter())
        record = _record("calling %s", "token=abc", exc_info=_exc_info("token=abc"))

        # Act
        result = log_filter.filter(record)

        # Assert
        assert result is not record
        assert record.getMessage() == "calling token=abc"
        assert record.exc_text is None

    def test_traceback_is_redacted(self) -> None:
        # Arrange
        log_filter = RedactingFilter(_token_redactor(), logging.Formatter())
        record = _record("failed", exc_info=_exc_info("bad token=abc"))

        # Act
        result = log_filter.filter(record)

        # Assert
        assert result.exc_text is not None
        assert "token=abc" not in result.exc_text
        assert f"SensorError: bad {REDACTED}" in result.exc_text

    def test_pre_formatted_traceback_is_redacted(self) -> None:
        # Arrange — exc_text already cached by an earlier handler's formatter
        log_filter = RedactingFilter(_token_redactor(), logging.Formatter())
        record = _record("failed")
        record.exc_text = "Traceback: token=abc"

        # Act
        result = log_filter.filter(record)

        # Assert
        assert result.exc_text == f"Traceback: {REDACTED}"

    def test_stack_info_is_redacted(self) -> None:
        # Arrange
        log_filter = RedactingFilter(_token_redactor(), logging.Formatter())
        record = _record("here", stack_info="Stack: url?token=abc")

        # Act
        result = log_filter.filter(record)

        # Assert
        assert result.stack_info == f"Stack: url?{REDACTED}"


class TestJsonFormatterExcText:
    """JsonFormatter prefers a pre-formatted traceback (ADR-085)."""

    def test_exc_text_wins_over_formatting_exc_info(self) -> None:
        # Arrange
        record = _record("failed", exc_info=_exc_info("token=abc"))
        record.exc_text = "already formatted"

        # Act
        entry = json.loads(JsonFormatter(service="svc").format(record))

        # Assert
        assert entry["exception"] == "already formatted"


# ---------------------------------------------------------------------------
# configure_logging(redact=...)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_restore_root_logger")
class TestConfigureLoggingRedact:
    """configure_logging installs the filter on every handler it creates."""

    def test_no_filter_without_redact(self) -> None:
        # Act
        configure_logging(LoggingSettings(), service="svc")

        # Assert
        assert all(not h.filters for h in logging.getLogger().handlers)

    def test_every_handler_gets_the_filter(self, tmp_path: Path) -> None:
        # Arrange
        settings = LoggingSettings(file=str(tmp_path / "app.log"))

        # Act
        configure_logging(settings, service="svc", redact=[REDACTION_SENTINEL])

        # Assert
        handlers = logging.getLogger().handlers
        assert len(handlers) == 2
        for handler in handlers:
            assert any(isinstance(f, RedactingFilter) for f in handler.filters)

    def test_json_line_redacts_message_and_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        stream = io.StringIO()
        monkeypatch.setattr(sys, "stderr", stream)
        configure_logging(
            LoggingSettings(format="json"), service="svc", redact=[REDACTION_SENTINEL]
        )
        log = logging.getLogger("app.redact")

        # Act
        try:
            raise SensorError(f"login failed for {REDACTION_SENTINEL}")
        except SensorError:
            log.exception("auth with %s", REDACTION_SENTINEL)

        # Assert
        entry = json.loads(stream.getvalue().strip())
        assert entry["message"] == f"auth with {REDACTED}"
        assert REDACTION_SENTINEL not in entry["exception"]
        assert f"login failed for {REDACTED}" in entry["exception"]

    @pytest.mark.parametrize("format", ["text", "json"])
    def test_empty_redacted_traceback_does_not_fall_back_to_exc_info(
        self, monkeypatch: pytest.MonkeyPatch, format: Literal["text", "json"]
    ) -> None:
        stream = io.StringIO()
        monkeypatch.setattr(sys, "stderr", stream)
        configure_logging(
            LoggingSettings(format=format), service="svc", redact=lambda _text: ""
        )

        try:
            raise SensorError(f"failure {REDACTION_SENTINEL}")
        except SensorError:
            logging.getLogger("app.redact").exception("request failed")

        assert REDACTION_SENTINEL not in stream.getvalue()

    def test_file_handler_output_is_redacted(self, tmp_path: Path) -> None:
        # Arrange
        log_file = tmp_path / "app.log"
        configure_logging(
            LoggingSettings(file=str(log_file)),
            service="svc",
            redact=[REDACTION_SENTINEL],
        )

        # Act
        logging.getLogger("app.redact").warning("secret is %s", REDACTION_SENTINEL)
        for handler in logging.getLogger().handlers:
            handler.flush()

        # Assert
        text = log_file.read_text(encoding="utf-8")
        assert f"secret is {REDACTED}" in text
        assert REDACTION_SENTINEL not in text

    def test_other_handlers_see_the_original_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        configure_logging(LoggingSettings(), service="svc", redact=[REDACTION_SENTINEL])
        seen: list[str] = []

        class _Capture(logging.Handler):
            @override
            def emit(self, record: logging.LogRecord) -> None:
                seen.append(record.getMessage())

        logging.getLogger().addHandler(_Capture())

        # Act
        logging.getLogger("app.redact").warning("secret is %s", REDACTION_SENTINEL)

        # Assert
        assert seen == [f"secret is {REDACTION_SENTINEL}"]

    def test_invalid_redact_raises_before_touching_handlers(self) -> None:
        # Arrange
        before = logging.getLogger().handlers[:]

        # Act / Assert
        with pytest.raises(ValueError, match="invalid redact pattern"):
            configure_logging(LoggingSettings(), service="svc", redact=["("])
        assert logging.getLogger().handlers == before


# ---------------------------------------------------------------------------
# Error payloads
# ---------------------------------------------------------------------------


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class TestErrorPayloadRedaction:
    """Decision table: the redactor runs only on disclosed messages."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param({"verbose": True}, f"login {REDACTED}", id="verbose"),
            pytest.param(
                {"disclose_messages_for": frozenset({SensorError})},
                f"login {REDACTED}",
                id="disclose-set",
            ),
            pytest.param(
                {"error_type_map": {SensorError: "sensor"}},
                f"login {REDACTED}",
                id="legacy-map",
            ),
            pytest.param({}, "SensorError", id="undisclosed"),
            pytest.param(
                {
                    "error_type_map": {SensorError: "sensor"},
                    "disclose_messages_for": frozenset(),
                },
                "SensorError",
                id="mapped-but-not-disclosed",
            ),
        ],
    )
    def test_message_by_disclosure(self, kwargs: dict[str, Any], expected: str) -> None:
        # Arrange
        redactor = build_redactor([REDACTION_SENTINEL])

        # Act
        payload = build_error_payload(
            SensorError(f"login {REDACTION_SENTINEL}"),
            clock=_clock,
            redact=redactor,
            **kwargs,
        )

        # Assert
        assert payload.message == expected

    def test_redactor_not_called_for_undisclosed_error(self) -> None:
        # Arrange
        calls: list[str] = []

        def spy(text: str) -> str:
            calls.append(text)
            return text

        # Act
        build_error_payload(SensorError(REDACTION_SENTINEL), clock=_clock, redact=spy)

        # Assert
        assert calls == []

    def test_disclosed_message_without_redactor_is_unchanged(self) -> None:
        # Act
        payload = build_error_payload(
            SensorError(REDACTION_SENTINEL), clock=_clock, verbose=True
        )

        # Assert
        assert payload.message == REDACTION_SENTINEL

    async def test_error_publisher_applies_redact(self) -> None:
        # Arrange
        mqtt = MockMqttClient()
        publisher = ErrorPublisher(
            mqtt=mqtt,
            topic_prefix="app",
            verbose=True,
            redact=build_redactor([REDACTION_SENTINEL]),
        )

        # Act
        await publisher.publish(SensorError(f"login {REDACTION_SENTINEL}"))

        # Assert
        payload = json.loads(mqtt.get_messages_for("app/error")[0][0])
        assert payload["message"] == f"login {REDACTED}"

    def test_create_services_threads_redact(self) -> None:
        # Arrange
        redactor = build_redactor([REDACTION_SENTINEL])

        # Act
        _, publisher = create_services(
            MockMqttClient(), "app", "1.0", FakeClock(), redact=redactor
        )

        # Assert
        assert publisher.redact is redactor


# ---------------------------------------------------------------------------
# App(redact=...)
# ---------------------------------------------------------------------------


class TestAppRedactParameter:
    """App validates redact when it is created."""

    def test_default_is_off(self) -> None:
        # Act
        app = App(name="redactapp")

        # Assert
        assert app._redactor is None

    def test_patterns_are_compiled_at_init(self) -> None:
        # Act
        app = App(name="redactapp", redact=[REDACTION_SENTINEL])

        # Assert
        assert app._redactor is not None
        assert app._redactor(REDACTION_SENTINEL) == REDACTED

    def test_invalid_pattern_fails_at_init(self) -> None:
        # Act / Assert
        with pytest.raises(ValueError, match="invalid redact pattern"):
            App(name="redactapp", redact=["("])

    def test_wrong_type_fails_at_init(self) -> None:
        # Act / Assert
        with pytest.raises(TypeError, match="wrap it in a list"):
            App(name="redactapp", redact=REDACTION_SENTINEL)
