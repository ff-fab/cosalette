"""Test factory for framework Settings.

Provides :func:`make_settings` — a convenience factory that creates
:class:`~cosalette._settings.Settings` instances without depending on
``.env`` files or real environment variables.

See Also:
    ADR-007 for testing strategy decisions.
"""

from __future__ import annotations

from typing import Any, override

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from cosalette._settings import Settings


class _IsolatedSettings(Settings):
    """Settings subclass that ignores all ambient configuration sources.

    Overrides :meth:`settings_customise_sources` to return only
    ``init_settings``, stripping ``EnvSettingsSource``,
    ``DotEnvSettingsSource``, and ``SecretsSettingsSource``.
    This ensures tests are fully deterministic regardless of the
    host environment.
    """

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def _allowed_override_keys() -> frozenset[str]:
    """Return the keyword names ``make_settings`` legitimately accepts.

    That is every :class:`Settings` field, referenced by its Python name
    *or* its populate alias (``schema`` for the ``schema_`` field), plus
    the ``_config_file`` runtime kwarg :meth:`Settings.__init__` consumes.
    Derived from ``model_fields`` so it tracks the model rather than a
    hand-maintained list.
    """
    keys: set[str] = {"_config_file"}
    for name, field in Settings.model_fields.items():
        keys.add(name)
        if field.alias is not None:
            keys.add(field.alias)
    return frozenset(keys)


def make_settings(**overrides: Any) -> Settings:
    """Create a ``Settings`` instance with sensible test defaults.

    Instantiates an :class:`_IsolatedSettings` subclass whose only
    configuration source is ``init_settings``.  This means the
    factory ignores ``os.environ``, ``.env`` files, and secret
    directories — tests see only model defaults plus any explicit
    *overrides*.

    Parameters:
        **overrides: Keyword arguments forwarded to the ``Settings``
            constructor.  Any field not provided falls back to the
            model defaults (e.g. ``mqtt.host="localhost"``).

    Returns:
        A fully initialised :class:`Settings` ready for test use.

    Raises:
        TypeError: If an override names neither a ``Settings`` field nor
            the ``_config_file`` runtime kwarg.  ``Settings`` itself uses
            ``extra="ignore"`` (it reads the whole unprefixed environment),
            so a typo'd or unsupported keyword would otherwise be swallowed
            silently — a test running against defaults it never asked for.

    Example::

        settings = make_settings()
        assert settings.mqtt.host == "localhost"

        from cosalette._settings import MqttSettings
        custom = make_settings(mqtt=MqttSettings(host="broker.test"))
        assert custom.mqtt.host == "broker.test"
    """
    allowed_keys = _allowed_override_keys()
    unknown = overrides.keys() - allowed_keys
    if unknown:
        allowed = ", ".join(sorted(allowed_keys))
        offending = ", ".join(sorted(unknown))
        msg = (
            f"make_settings() got unexpected keyword argument(s): {offending}. "
            f"Valid settings overrides are: {allowed}."
        )
        raise TypeError(msg)
    # _env_file is a valid pydantic-settings runtime kwarg that disables
    # dotenv loading.
    return _IsolatedSettings(_env_file=None, **overrides)
