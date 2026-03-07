"""Tests for cosalette._json — centralised JSON helpers.

Test Techniques Used:
    - Specification-based Testing: return types, round-trip fidelity
    - Compatibility Testing: output matches stdlib json for stores migration
    - Exception Safety: JSONDecodeError hierarchy matches expectations
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cosalette._json import JSONDecodeError, dumps, dumps_pretty, loads

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# dumps
# ---------------------------------------------------------------------------


class TestDumps:
    """Tests for the ``dumps`` wrapper."""

    def test_dumps_returns_str(self) -> None:
        """dumps() must return a str, not bytes."""
        result = dumps({"a": 1})
        assert isinstance(result, str)

    def test_dumps_round_trip(self) -> None:
        """dumps → loads must recover the original dict."""
        original = {"key": "value", "number": 42, "nested": [1, 2, 3]}
        assert loads(dumps(original)) == original

    def test_dumps_with_default(self) -> None:
        """A custom *default* callback serializes otherwise-unserializable types."""
        dt = datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC)
        result = dumps({"ts": dt}, default=str)
        parsed = loads(result)
        assert parsed["ts"] == dt.isoformat()


# ---------------------------------------------------------------------------
# dumps_pretty
# ---------------------------------------------------------------------------


class TestDumpsPretty:
    """Tests for the ``dumps_pretty`` wrapper."""

    def test_dumps_pretty_indentation(self) -> None:
        """Output must contain 2-space indentation."""
        result = dumps_pretty({"a": 1})
        # orjson indents with two spaces; verify the key is indented
        assert '  "a"' in result

    def test_dumps_pretty_matches_stdlib_indent2(self) -> None:
        """dumps_pretty must produce identical output to json.dumps(indent=2).

        This is critical for the stores migration — existing JSON files were
        written with the stdlib, and we must not introduce spurious diffs.
        """
        obj = {"alpha": 1, "beta": [True, False, None], "gamma": "hello"}
        expected = json.dumps(obj, indent=2)
        assert dumps_pretty(obj) == expected

    def test_dumps_pretty_non_ascii_emits_utf8(self) -> None:
        """orjson emits raw UTF-8 for non-ASCII, unlike stdlib's ensure_ascii default.

        This is intentional — RFC 8259 Section 8.1 mandates UTF-8 encoding.
        Store files may differ from legacy stdlib output for non-ASCII values,
        but the result is valid, spec-compliant JSON.
        """
        obj = {"name": "Außentemperatur", "city": "Zürich"}
        result = dumps_pretty(obj)
        # orjson preserves raw UTF-8 characters
        assert "Außentemperatur" in result
        assert "Zürich" in result
        # stdlib would escape these to \uXXXX sequences
        stdlib_result = json.dumps(obj, indent=2)
        assert "Au\\u00df" in stdlib_result  # stdlib escapes ß


# ---------------------------------------------------------------------------
# loads
# ---------------------------------------------------------------------------


class TestLoads:
    """Tests for the ``loads`` wrapper."""

    def test_loads_accepts_str(self) -> None:
        """loads() must accept a str."""
        assert loads("{}") == {}

    def test_loads_accepts_bytes(self) -> None:
        """loads() must accept bytes."""
        assert loads(b'{"x": 1}') == {"x": 1}

    def test_loads_raises_json_decode_error(self) -> None:
        """Malformed input must raise JSONDecodeError (a ValueError subclass)."""
        with pytest.raises(JSONDecodeError):
            loads("{bad")

        # Also verify the stdlib invariant: JSONDecodeError is a ValueError
        assert issubclass(JSONDecodeError, ValueError)


# ---------------------------------------------------------------------------
# JSONDecodeError compatibility
# ---------------------------------------------------------------------------


class TestJSONDecodeErrorCompat:
    """Verify JSONDecodeError interoperability with the stdlib."""

    def test_json_decode_error_is_subclass_of_stdlib(self) -> None:
        """orjson.JSONDecodeError must be catchable as json.JSONDecodeError.

        This matters for stores code that currently catches the stdlib exception.
        """
        with pytest.raises(json.JSONDecodeError):
            loads("{bad")
