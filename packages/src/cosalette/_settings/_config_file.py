"""Config-file settings source for cosalette.

Provides a pydantic-settings source that loads values from a TOML, YAML,
or JSON file.  The file is only consulted when ``config_file`` is set in
the subclass ``model_config`` or supplied at runtime via the ``_config_file``
constructor keyword argument.

Precedence (highest → lowest):
    init_settings > env_settings > dotenv_settings > config_file > secrets
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, override

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

# ---------------------------------------------------------------------------
# Sentinel — distinguishes "not supplied" from explicit None
# ---------------------------------------------------------------------------

_UNSET: Final = object()
"""Sentinel used to detect when _config_file was not passed at all."""

# ---------------------------------------------------------------------------
# ContextVar — carries the runtime override into settings_customise_sources
# ---------------------------------------------------------------------------

_config_file_override: ContextVar[object] = ContextVar(
    "cosalette_config_file_override", default=_UNSET
)

# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


@dataclass
class SettingsLoadError(Exception):
    """Raised when a config file cannot be loaded or parsed."""

    path: Path
    message: str
    hint: str | None = None

    @override
    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n\n{self.hint}"
        return self.message

    @classmethod
    def not_found(cls, path: Path) -> SettingsLoadError:
        """Config file path is configured but the file does not exist."""
        return cls(path=path, message=f"config file not found: {path}")

    @classmethod
    def parse_failed(cls, path: Path, detail: str) -> SettingsLoadError:
        """Config file exists but cannot be parsed."""
        return cls(
            path=path,
            message=f"could not load configuration file '{path}': {detail}",
        )

    @classmethod
    def missing_dependency(
        cls, path: Path, package: str, extra: str
    ) -> SettingsLoadError:
        """Optional dependency required by the file format is not installed."""
        return cls(
            path=path,
            message=f"{package} is required to read '{path}'.",
            hint=f"Install with: pip install cosalette[{extra}]",
        )


# ---------------------------------------------------------------------------
# Settings source
# ---------------------------------------------------------------------------


class _ConfigFileSource(PydanticBaseSettingsSource):
    """Loads settings from a single TOML / YAML / JSON file.

    Raises :exc:`SettingsLoadError` on misconfiguration (missing file,
    parse error, unsupported format, absent optional dependency).
    Silent — returning empty data — when no file is configured.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        *,
        override: object = _UNSET,
    ) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}

        # Resolve effective path
        if override is not _UNSET:
            raw = override
        else:
            raw = settings_cls.model_config.get("config_file")  # type: ignore[call-overload]

        if not raw:
            # None / empty / not set → silent no-op
            return

        path = Path(str(raw))

        if not path.is_file():
            raise SettingsLoadError.not_found(path)

        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()

        if suffix == ".toml":
            import tomllib

            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                raise SettingsLoadError.parse_failed(path, str(exc)) from exc

        elif suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-not-found]
            except ImportError as exc:
                raise SettingsLoadError.missing_dependency(
                    path, "PyYAML", "config-yaml"
                ) from exc
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise SettingsLoadError.parse_failed(path, str(exc)) from exc
            if data is None:
                data = {}

        elif suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SettingsLoadError.parse_failed(path, str(exc)) from exc

        else:
            raise SettingsLoadError(
                path=path,
                message=(
                    f"unsupported config file format '{path.suffix}' for '{path}';"
                    " supported: .toml, .yaml, .yml, .json"
                ),
            )

        if not isinstance(data, dict):
            raise SettingsLoadError.parse_failed(
                path, "top-level must be a table/object"
            )

        self._data = data

    @override
    def get_field_value(
        self,
        field: FieldInfo,  # noqa: ARG002
        field_name: str,
    ) -> tuple[Any, str, bool]:
        return (self._data.get(field_name), field_name, False)

    @override
    def __call__(self) -> dict[str, Any]:
        return self._data
