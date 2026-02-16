"""Test factory for framework Settings.

Provides :func:`make_settings` — a convenience factory that creates
:class:`~cosalette._settings.Settings` instances without depending on
``.env`` files or real environment variables.

See Also:
    ADR-007 for testing strategy decisions.
"""

from __future__ import annotations

from typing import Any

from cosalette._settings import Settings


def make_settings(**overrides: Any) -> Settings:
    """Create a ``Settings`` instance with sensible test defaults.

    Uses ``_env_file=None`` so that tests never accidentally load a
    real ``.env`` file from the working directory.

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
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
