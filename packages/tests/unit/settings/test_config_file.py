"""Unit tests for cosalette._settings._config_file.

Test Techniques Used:
    - Equivalence Partitioning: supported file formats (.toml, .yaml, .json)
      vs unsupported suffix; file present vs missing.
    - Decision Table: combinations of model_config default and runtime override.
    - Error Guessing: missing file, malformed content, absent optional dep.
    - Branch/Condition Coverage: each dispatch branch in _ConfigFileSource.
    - Boundary Value Analysis: empty YAML document (None result from safe_load).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic_settings import SettingsConfigDict

from cosalette._settings import Settings
from cosalette._settings._config_file import (
    SettingsLoadError,
    _ConfigFileSource,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal probe Settings subclass
# ---------------------------------------------------------------------------


class _ProbeSettings(Settings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=None,
        extra="ignore",
        config_file=None,  # ty: ignore[invalid-key]
    )
    value: str = "default"
    count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# TOML
# ---------------------------------------------------------------------------


class TestTomlSource:
    """Config values loaded from a TOML file.

    Technique: Equivalence Partitioning — TOML format branch.
    """

    def test_toml_values_used_when_no_env(self, tmp_path: Path) -> None:
        """File values are returned when no env var overrides them."""
        cfg = _write(tmp_path, "app.toml", 'value = "from_toml"\ncount = 7\n')

        s = _ProbeSettings(_config_file=cfg)

        assert s.value == "from_toml"
        assert s.count == 7

    def test_env_overrides_toml_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variable takes precedence over the config file.

        Technique: Decision Table — env > file.
        """
        cfg = _write(tmp_path, "app.toml", 'value = "from_toml"\n')
        monkeypatch.setenv("VALUE", "from_env")

        s = _ProbeSettings(_config_file=cfg)

        assert s.value == "from_env"

    def test_malformed_toml_raises_settings_load_error(self, tmp_path: Path) -> None:
        """Malformed TOML content raises SettingsLoadError with parse_failed message.

        Technique: Error Guessing.
        """
        cfg = _write(tmp_path, "app.toml", "[[broken\n")

        with pytest.raises(SettingsLoadError) as exc_info:
            _ProbeSettings(_config_file=cfg)

        assert "could not load configuration file" in str(exc_info.value)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


class TestJsonSource:
    """Config values loaded from a JSON file.

    Technique: Equivalence Partitioning — JSON format branch.
    """

    def test_json_values_parsed_correctly(self, tmp_path: Path) -> None:
        """Valid JSON file populates settings fields."""
        cfg = _write(tmp_path, "app.json", '{"value": "from_json", "count": 3}')

        s = _ProbeSettings(_config_file=cfg)

        assert s.value == "from_json"
        assert s.count == 3

    def test_malformed_json_raises_settings_load_error(self, tmp_path: Path) -> None:
        """Malformed JSON raises SettingsLoadError."""
        cfg = _write(tmp_path, "app.json", "{bad json}")

        with pytest.raises(SettingsLoadError) as exc_info:
            _ProbeSettings(_config_file=cfg)

        assert "could not load configuration file" in str(exc_info.value)


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------


class TestYamlSource:
    """Config values loaded from a YAML file.

    Technique: Equivalence Partitioning — YAML format branch.
    """

    def test_yaml_values_parsed_when_pyyaml_installed(self, tmp_path: Path) -> None:
        """YAML file populates settings when PyYAML is available.

        Technique: Branch/Condition Coverage — yaml import succeeds.
        """
        yaml = pytest.importorskip("yaml")
        _ = yaml  # importorskip returns the module; we just need the skip guard

        cfg = _write(tmp_path, "app.yaml", "value: from_yaml\ncount: 5\n")

        s = _ProbeSettings(_config_file=cfg)

        assert s.value == "from_yaml"
        assert s.count == 5

    def test_yaml_missing_dep_raises_settings_load_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing PyYAML raises SettingsLoadError with install hint.

        Technique: Error Guessing — absent optional dependency.
        """
        cfg = _write(tmp_path, "app.yaml", "value: x\n")
        monkeypatch.setitem(sys.modules, "yaml", None)  # type: ignore[arg-type]

        with pytest.raises(SettingsLoadError) as exc_info:
            _ProbeSettings(_config_file=cfg)

        err = exc_info.value
        assert "PyYAML" in str(err)
        assert "pip install cosalette[config-yaml]" in (err.hint or "")

    def test_empty_yaml_document_is_treated_as_empty_dict(self, tmp_path: Path) -> None:
        """An empty YAML document (safe_load → None) does not raise.

        Technique: Boundary Value Analysis — empty YAML.
        """
        pytest.importorskip("yaml")
        cfg = _write(tmp_path, "app.yaml", "")

        # Should not raise; defaults are used
        s = _ProbeSettings(_config_file=cfg)
        assert s.value == "default"


# ---------------------------------------------------------------------------
# Unsupported format
# ---------------------------------------------------------------------------


class TestUnsupportedFormat:
    """Unsupported file extension raises SettingsLoadError.

    Technique: Error Guessing.
    """

    def test_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        cfg = _write(tmp_path, "app.ini", "[section]\nkey=val\n")

        with pytest.raises(SettingsLoadError) as exc_info:
            _ProbeSettings(_config_file=cfg)

        assert "unsupported config file format" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


class TestMissingFile:
    """A configured but absent file raises SettingsLoadError.

    Technique: Error Guessing — operator misconfiguration.
    """

    def test_missing_path_raises_not_found(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no_such_file.toml"

        with pytest.raises(SettingsLoadError) as exc_info:
            _ProbeSettings(_config_file=nonexistent)

        assert "config file not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Runtime override vs model_config default
# ---------------------------------------------------------------------------


class TestRuntimeOverride:
    """_config_file kwarg takes precedence over model_config.config_file.

    Technique: Decision Table — init override vs config default.
    """

    def test_runtime_override_used_over_model_config(self, tmp_path: Path) -> None:
        """Runtime _config_file overrides the class-level config_file=None."""
        cfg = _write(tmp_path, "override.toml", 'value = "runtime"\n')

        s = _ProbeSettings(_config_file=cfg)

        assert s.value == "runtime"

    def test_explicit_none_disables_file_source(self, tmp_path: Path) -> None:
        """Passing _config_file=None disables the source even when model_config has a path.

        Technique: Decision Table — explicit None short-circuits.
        """  # noqa: E501

        class _WithDefault(Settings):
            model_config = SettingsConfigDict(
                env_nested_delimiter="__",
                env_file=None,
                extra="ignore",
                config_file=str(tmp_path / "missing.toml"),  # ty: ignore[invalid-key]
            )
            value: str = "default"

        # Should not raise even though missing.toml doesn't exist
        s = _WithDefault(_config_file=None)
        assert s.value == "default"


# ---------------------------------------------------------------------------
# No config file at all (default behaviour)
# ---------------------------------------------------------------------------


class TestNoConfigFile:
    """Default state: no config_file, no override → silent, no I/O.

    Technique: Specification-based Testing — backward-compatibility guarantee.
    """

    def test_default_settings_unchanged_without_config_file(self) -> None:
        """Existing behaviour is unaffected when config_file is not configured."""
        s = _ProbeSettings()

        assert s.value == "default"
        assert s.count == 0

    def test_config_file_source_returns_empty_dict_when_unset(self) -> None:
        """_ConfigFileSource with no path configured returns empty dict."""
        source = _ConfigFileSource(_ProbeSettings)

        result = source()

        assert result == {}


# ---------------------------------------------------------------------------
# Non-mapping top-level
# ---------------------------------------------------------------------------


class TestNonMappingTopLevel:
    """A config file whose top-level is not a dict raises SettingsLoadError.

    Technique: Error Guessing.
    """

    def test_toml_array_at_root_raises(self, tmp_path: Path) -> None:
        # TOML can't produce an array at root; use JSON which can
        cfg = _write(tmp_path, "app.json", '["a", "b"]')

        with pytest.raises(SettingsLoadError) as exc_info:
            _ProbeSettings(_config_file=cfg)

        assert "top-level must be a table/object" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SettingsLoadError API
# ---------------------------------------------------------------------------


class TestSettingsLoadErrorApi:
    """SettingsLoadError class methods and __str__ behaviour.

    Technique: Specification-based Testing.
    """

    def test_not_found_message(self, tmp_path: Path) -> None:
        p = tmp_path / "x.toml"
        err = SettingsLoadError.not_found(p)
        assert str(err) == f"config file not found: {p}"
        assert err.hint is None

    def test_parse_failed_message(self, tmp_path: Path) -> None:
        p = tmp_path / "x.toml"
        err = SettingsLoadError.parse_failed(p, "bad syntax")
        assert "could not load configuration file" in str(err)
        assert "bad syntax" in str(err)

    def test_missing_dependency_has_hint(self, tmp_path: Path) -> None:
        p = tmp_path / "x.yaml"
        err = SettingsLoadError.missing_dependency(p, "PyYAML", "config-yaml")
        assert "PyYAML" in str(err)
        assert err.hint is not None
        assert "cosalette[config-yaml]" in err.hint

    def test_str_with_hint_includes_hint(self, tmp_path: Path) -> None:
        p = tmp_path / "x.yaml"
        err = SettingsLoadError(path=p, message="msg", hint="hint text")
        assert "hint text" in str(err)

    def test_str_without_hint_omits_newlines(self, tmp_path: Path) -> None:
        p = tmp_path / "x.toml"
        err = SettingsLoadError(path=p, message="just msg")
        assert str(err) == "just msg"


# ---------------------------------------------------------------------------
# Sentinel semantics: model_config.config_file vs _config_file override
# ---------------------------------------------------------------------------


class TestSentinelSemantics:
    """model_config.config_file is honored when _config_file is not passed.

    Technique: Decision Table — _UNSET sentinel vs explicit None vs explicit path.
    """

    def test_model_config_honored_when_config_file_omitted(
        self, tmp_path: Path
    ) -> None:
        """_config_file absent → model_config.config_file is used."""
        cfg = _write(tmp_path, "defaults.toml", 'value = "from_model_config"\n')

        class _WithDefault(Settings):
            model_config = SettingsConfigDict(
                env_nested_delimiter="__",
                env_file=None,
                extra="ignore",
                config_file=str(cfg),  # ty: ignore[invalid-key]
            )
            value: str = "default"

        s = _WithDefault(_env_file=None)
        assert s.value == "from_model_config"

    def test_explicit_none_disables_when_model_config_has_path(
        self, tmp_path: Path
    ) -> None:
        """_config_file=None disables the source even though model_config has a path."""
        cfg = _write(tmp_path, "defaults.toml", 'value = "from_model_config"\n')

        class _WithDefault(Settings):
            model_config = SettingsConfigDict(
                env_nested_delimiter="__",
                env_file=None,
                extra="ignore",
                config_file=str(cfg),  # ty: ignore[invalid-key]
            )
            value: str = "default"

        s = _WithDefault(_env_file=None, _config_file=None)
        assert s.value == "default"

    def test_explicit_path_overrides_model_config(self, tmp_path: Path) -> None:
        """_config_file=<path> uses the override, not model_config.config_file."""
        default_cfg = _write(tmp_path, "model_default.toml", 'value = "from_default"\n')
        override_cfg = _write(tmp_path, "override.toml", 'value = "from_override"\n')

        class _WithDefault(Settings):
            model_config = SettingsConfigDict(
                env_nested_delimiter="__",
                env_file=None,
                extra="ignore",
                config_file=str(default_cfg),  # ty: ignore[invalid-key]
            )
            value: str = "default"

        s = _WithDefault(_env_file=None, _config_file=str(override_cfg))
        assert s.value == "from_override"


# ---------------------------------------------------------------------------
# Nested per-field merge
# ---------------------------------------------------------------------------


class TestNestedPerFieldMerge:
    """File supplies nested table; env overrides one field; both survive.

    Technique: Decision Table — config_file vs env precedence for nested models.
    Acceptance criterion 4: per-field deep merge.
    """

    def test_env_overrides_one_nested_field_rest_from_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mqtt.host comes from file; mqtt.port comes from env override."""
        toml_content = '[mqtt]\nhost = "file-broker"\nport = 1234\n'
        cfg = _write(tmp_path, "app.toml", toml_content)

        monkeypatch.setenv("MQTT__PORT", "9999")

        s = _ProbeSettings(_env_file=None, _config_file=str(cfg))

        assert s.mqtt.host == "file-broker"
        assert s.mqtt.port == 9999


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


class TestAliasResolution:
    """[schema] TOML table maps to Settings.schema_ via its alias.

    Technique: Specification-based Testing — alias= field mapping.
    """

    def test_schema_table_populates_schema_field(self, tmp_path: Path) -> None:
        """[schema] in TOML resolves to settings.schema_ via alias."""
        toml_content = '[schema]\nenforcement = "warn"\n'
        cfg = _write(tmp_path, "app.toml", toml_content)

        s = _ProbeSettings(_env_file=None, _config_file=str(cfg))

        assert s.schema_.enforcement == "warn"
