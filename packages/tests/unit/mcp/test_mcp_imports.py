"""Tests for the MCP import allowlist (MCP-01).

Test Techniques Used:
- Boundary Value Analysis: prefix matching at the module-boundary ('.')
- Decision Table: allowlist unset / set-and-matching / set-and-not-matching
- Error Guessing: prefix-collision bypass attempts (``myapp`` vs ``myapp_evil``)
"""

from __future__ import annotations

import pytest

from cosalette._mcp._imports import ALLOW_ENV, import_from_spec

# A real, always-importable spec used for the "allowed" success cases.
_REAL_SPEC = "cosalette._mcp._imports:import_from_spec"


class TestImportAllowlistDenyByDefault:
    """Unset/empty allowlist refuses every gated import."""

    def test_unset_allowlist_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With the env var unset, a gated import is refused before running code."""
        # Arrange
        monkeypatch.delenv(ALLOW_ENV, raising=False)

        # Act
        obj, err = import_from_spec(_REAL_SPEC)

        # Assert
        assert obj is None
        assert err is not None
        assert "Refusing to import" in err
        assert ALLOW_ENV in err

    def test_empty_allowlist_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty/whitespace env value is treated as unset (deny all)."""
        monkeypatch.setenv(ALLOW_ENV, "  ,  ")

        obj, err = import_from_spec(_REAL_SPEC)

        assert obj is None
        assert err is not None and "Refusing to import" in err


class TestImportAllowlistMatching:
    """Boundary-aware prefix matching."""

    def test_exact_prefix_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An exact module-prefix match permits the import."""
        monkeypatch.setenv(ALLOW_ENV, "cosalette")

        obj, err = import_from_spec(_REAL_SPEC)

        assert err is None
        assert obj is import_from_spec

    def test_dotted_prefix_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A prefix permits deeper submodules under the '.' boundary."""
        monkeypatch.setenv(ALLOW_ENV, "cosalette._mcp")

        obj, err = import_from_spec(_REAL_SPEC)

        assert err is None
        assert obj is import_from_spec

    def test_prefix_collision_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``myapp`` must NOT allow ``myapp_evil`` (boundary-aware matching)."""
        monkeypatch.setenv(ALLOW_ENV, "myapp")

        obj, err = import_from_spec("myapp_evil.plugin:app")

        assert obj is None
        assert err is not None
        assert "Refusing to import" in err
        assert "myapp_evil.plugin" in err

    def test_unlisted_module_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A module outside every allowed prefix is refused."""
        monkeypatch.setenv(ALLOW_ENV, "myapp")

        obj, err = import_from_spec("other.module:app")

        assert obj is None
        assert err is not None and "Refusing to import" in err


class TestImportAllowlistBypass:
    """The gate is skippable only via the explicit developer-CLI opt-out."""

    def test_enforce_false_skips_allowlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``enforce_allowlist=False`` reaches the real import even when denied.

        Technique: State Transition — CLI path bypasses the MCP gate and then
        fails on the normal 'module not found' path instead of the allowlist.
        """
        monkeypatch.delenv(ALLOW_ENV, raising=False)

        obj, err = import_from_spec(
            "definitely_not_a_real_module:app", enforce_allowlist=False
        )

        assert obj is None
        assert err is not None
        assert "Refusing to import" not in err
        assert "Could not import module" in err

    def test_invalid_spec_reported_before_allowlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed spec is rejected on format before the allowlist runs."""
        monkeypatch.delenv(ALLOW_ENV, raising=False)

        obj, err = import_from_spec("no_colon_here")

        assert obj is None
        assert err is not None
        assert "Expected format" in err
