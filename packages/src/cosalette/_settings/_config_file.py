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
    from collections.abc import Callable

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
# Not thread-safe in sync code; each asyncio task gets its own context.

# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


@dataclass
class SettingsLoadError(Exception):
    """Raised when a config file cannot be loaded or parsed."""

    path: Path
    message: str
    hint: str | None = None

    def __post_init__(self) -> None:
        # Populate Exception.args so repr(), logging, and exc.args[0] work correctly.
        Exception.__init__(self, self.message)

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
# Format parsers — one per suffix, dispatched by _parse_config_file
# ---------------------------------------------------------------------------


def _parse_toml(path: Path, text: str) -> Any:
    import tomllib

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsLoadError.parse_failed(path, str(exc)) from exc


def _parse_yaml(path: Path, text: str) -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SettingsLoadError.missing_dependency(
            path, "PyYAML", "config-yaml"
        ) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # Use only type + position; raw message may embed secret config lines.
        pos = getattr(exc, "problem_mark", "unknown position")
        detail = f"{type(exc).__name__} at {pos}"
        raise SettingsLoadError.parse_failed(path, detail) from exc
    return data if data is not None else {}


def _parse_json(path: Path, text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsLoadError.parse_failed(path, str(exc)) from exc


_PARSERS: Final[dict[str, Callable[[Path, str], Any]]] = {
    ".toml": _parse_toml,
    ".yaml": _parse_yaml,
    ".yml": _parse_yaml,
    ".json": _parse_json,
}


def _parse_config_file(path: Path) -> dict[str, Any]:
    """Read and parse *path* by suffix, returning a top-level mapping."""
    parser = _PARSERS.get(path.suffix.lower())
    if parser is None:
        raise SettingsLoadError(
            path=path,
            message=(
                f"unsupported config file format '{path.suffix}' for '{path}';"
                " supported: .toml, .yaml, .yml, .json"
            ),
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsLoadError.parse_failed(path, str(exc)) from exc
    data = parser(path, text)
    if not isinstance(data, dict):
        raise SettingsLoadError.parse_failed(path, "top-level must be a table/object")
    return data


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

        # Runtime override (explicit None disables) wins over model_config
        if override is not _UNSET:
            raw = override
        else:
            raw = settings_cls.model_config.get("config_file")  # type: ignore[call-overload]

        if not raw:
            return  # None / empty / not set → silent no-op

        path = Path(str(raw))
        if not path.is_file():
            raise SettingsLoadError.not_found(path)

        self._data = _parse_config_file(path)

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
