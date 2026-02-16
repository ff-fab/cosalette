"""Test factory for framework Settings.

Provides :func:`make_settings` — a convenience factory that creates
:class:`~cosalette._settings.Settings` instances without depending on
``.env`` files or real environment variables.

See Also:
    ADR-007 for testing strategy decisions.
"""

from __future__ import annotations

from typing import Any

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
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


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

    Example::

        settings = make_settings()
        assert settings.mqtt.host == "localhost"

        from cosalette._settings import MqttSettings
        custom = make_settings(mqtt=MqttSettings(host="broker.test"))
        assert custom.mqtt.host == "broker.test"
    """
    # _env_file is a valid pydantic-settings runtime kwarg that disables
    # dotenv loading, but it isn't reflected in the generated __init__
    # signature — hence the type: ignore.
    return _IsolatedSettings(_env_file=None, **overrides)  # type: ignore[call-arg]
