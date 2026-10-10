"""Settings-resolved schema generation without mutating the App (ADR-051).

The ADR-051 pipeline builds an App's Settings, resolves its adapters, runs its
configure hooks, expands settings-derived (ADR-023) ``name=``/``topic=`` specs
and prunes ``enabled=``-disabled registrations, so the AsyncAPI document
matches the entity set the running app registers.

Everything here works on copies of the App's registration lists and returns a
:class:`ResolvedApp` snapshot; the App itself is left as it was.  Failures are
raised as exceptions, never printed: the ``cosalette schema`` CLI formats them,
and :func:`resolved_asyncapi` (exported from :mod:`cosalette.schema`) lets them
propagate to its caller.

See Also:
    ADR-051 — Settings-aware schema pipeline for settings-derived entity names.
    ADR-072 — Prefix-aware AsyncAPI generation.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cosalette._settings._config_file import SettingsLoadError

# The wiring helpers are imported where they are used, so that importing
# cosalette.schema (which apps do at import time for consumer()) stays cheap.

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cosalette._app import App
    from cosalette._registration import (
        StreamRegistration,
        _CommandRegistration,
        _DeviceRegistration,
        _InboundRegistration,
        _TelemetryRegistration,
    )
    from cosalette._runners._periodic import _PeriodicRegistration
    from cosalette._settings import Settings


class SchemaBuildError(Exception):
    """An App's registrations cannot be turned into an AsyncAPI document.

    Raised by :func:`cosalette.schema.resolved_asyncapi` (and reported by the
    ``cosalette schema`` CLI as a configuration error) when:

    - settings resolution fails after the settings-derived specs are expanded:
      two registrations end up with the same name, or a telemetry handler that
      declares ``persist=`` survives ``enabled=`` resolution while schema
      generation runs without a store.  The original :exc:`ValueError` is the
      exception's ``__cause__``.
    - a registration still has a settings-derived ``name=`` or ``topic=`` that
      was not expanded, so its channel cannot be written down.
    """


@dataclass(frozen=True, slots=True)
class ResolvedApp:
    """The registrations of an App after ADR-051 settings resolution.

    A read-only snapshot: name specs are expanded and ``enabled=``-disabled
    registrations are gone.  It exposes the same read-only views as
    :class:`~cosalette.App` that schema generation and ``schema check`` read.
    """

    name: str
    version: str
    devices: tuple[_DeviceRegistration, ...]
    telemetry_registrations: tuple[_TelemetryRegistration, ...]
    commands: tuple[_CommandRegistration, ...]
    periodic_registrations: tuple[_PeriodicRegistration, ...]
    stream_registrations: tuple[StreamRegistration, ...]
    inbound_registrations: tuple[_InboundRegistration, ...]

    @property
    def registered_names(self) -> frozenset[str]:
        """All device/telemetry/command/periodic/stream names, as on App."""
        regs = itertools.chain(
            self.devices,
            self.telemetry_registrations,
            self.commands,
            self.periodic_registrations,
            self.stream_registrations,
        )
        return frozenset(reg.name for reg in regs)

    @property
    def root_names(self) -> frozenset[str]:
        """Names of root-level registrations (ADR-058), as on App."""
        regs = itertools.chain(
            self.devices,
            self.telemetry_registrations,
            self.commands,
            self.stream_registrations,
        )
        return frozenset(reg.name for reg in regs if reg.is_root)

    def asyncapi(self, *, topic_prefix: str | None = None) -> dict[str, Any]:
        """Build a new AsyncAPI 3.0.0 document from these registrations.

        Unlike :meth:`App.asyncapi`, nothing is cached: every call returns a
        new dict.
        """
        from cosalette._schema._asyncapi import build_app_asyncapi

        return build_app_asyncapi(self, topic_prefix=topic_prefix)


def build_settings(
    app: App, env_file: str | Path | None, config_file: str | Path | None
) -> Settings:
    """Construct *app*'s Settings from an optional ``.env`` / config file.

    Without *env_file*, pydantic-settings reads ``.env`` from the working
    directory if one exists.  An explicit *env_file* or *config_file* must
    exist.

    Raises:
        SettingsLoadError: When *env_file* or *config_file* does not exist,
            or the config file cannot be parsed.
        pydantic.ValidationError: When the resulting Settings are invalid.
    """
    if env_file is not None and not Path(env_file).is_file():
        raise SettingsLoadError(
            path=Path(env_file), message=f"env file not found: {env_file}"
        )
    settings_kwargs: dict[str, Any] = {"_env_file": env_file or ".env"}
    if config_file is not None:
        settings_kwargs["_config_file"] = config_file
    return app._settings_class(**settings_kwargs)


def check_names_expanded(
    registrations: Iterable[
        _DeviceRegistration
        | _TelemetryRegistration
        | _CommandRegistration
        | _InboundRegistration
    ],
) -> None:
    """Reject registrations whose settings-derived name or topic is unexpanded.

    Raises:
        SchemaBuildError: Listing every offending handler.
    """
    unexpanded = [
        reg
        for reg in registrations
        if reg.name_spec is not None or getattr(reg, "topic_spec", None) is not None
    ]
    if not unexpanded:
        return
    names_list = "\n".join(f"  - {reg.name!r}" for reg in unexpanded)
    msg = (
        "one or more registrations use a settings-derived name= "
        "(ADR-023) or topic= that cannot be represented in a static schema artifact. "
        "Use --resolve-settings to resolve their names and topics before "
        "generating the schema.\n\n"
        f"Offending handlers:\n{names_list}"
    )
    raise SchemaBuildError(msg)


def _snapshot(app: App) -> tuple[dict[str, Any], dict[str, Any]]:
    """Record *app*'s attributes and a shallow copy of each container's items."""
    attrs = dict(vars(app))
    contents = {
        key: value.copy()
        for key, value in attrs.items()
        if isinstance(value, (list, dict, set))
    }
    return attrs, contents


def _restore(app: App, snapshot: tuple[dict[str, Any], dict[str, Any]]) -> None:
    """Undo whatever user code did to *app*'s attributes since :func:`_snapshot`."""
    attrs, contents = snapshot
    for key in set(vars(app)) - set(attrs):
        object.__delattr__(app, key)
    for key, value in attrs.items():
        object.__setattr__(app, key, value)
    for key, items in contents.items():
        container = attrs[key]
        container.clear()
        if isinstance(container, list):
            container.extend(items)
        else:
            container.update(items)


async def resolve_app_async(
    app: App,
    env_file: str | Path | None = None,
    config_file: str | Path | None = None,
    *,
    _settings: Settings | None = None,
) -> tuple[ResolvedApp, str]:
    """Run the ADR-051 settings-resolving pipeline on copies of *app*'s lists.

    Mirrors the settings -> adapters -> configure-hooks -> expand ->
    resolve_enabled sequence in ``_app/_lifecycle.py::_run_async``, with two
    deliberate divergences: adapters are resolved with ``dry_run=True``
    regardless of the app's own default, and no Store is resolved
    (``store=None``) and no adapter lifecycle is entered.  Schema generation
    reads registrations; it does not run the application.

    Adapter factories and configure hooks are user code and still run.  An
    exception they raise propagates unchanged: it is an app bug, not a
    configuration error, and ``app.run()`` does not catch it either.  A hook
    that registers handlers on the App through a closure is honoured: those
    registrations are part of the result.  Afterwards every attribute of the
    App is put back as it was, so it is unchanged in every observable way,
    including its per-prefix ``asyncapi()`` cache, even when a hook raises.

    Returns:
        A ``(resolved, topic_prefix)`` pair.  *topic_prefix* is
        ``settings.mqtt.topic_prefix or app.name``, the prefix the runtime
        composes addresses from (ADR-072).

    Raises:
        SettingsLoadError: When the env or config file is missing or broken.
        pydantic.ValidationError: When the Settings are invalid.
        SchemaBuildError: When resolution fails after expansion, or a name
            spec is left unexpanded.
    """
    from cosalette._clock import SystemClock
    from cosalette._wiring import prepare_registrations
    from cosalette._wiring._bootstrap import (
        resolve_adapters_with_notifier,
        run_configure_hooks,
    )

    settings = (
        _settings
        if _settings is not None
        else build_settings(app, env_file, config_file)
    )

    snapshot = _snapshot(app)
    try:
        # dry_run=True requests the dry-run variant; falls back to the real
        # implementation when none is registered, so factories may still run.
        adapters, _notifier = resolve_adapters_with_notifier(
            app._adapters, settings, True
        )
        await run_configure_hooks(
            app._configure_hooks, settings, adapters, SystemClock()
        )
        telemetry = list(app._telemetry)
        devices = list(app._devices)
        commands = list(app._commands)
        periodic = list(app._periodic)
        streams = list(app._streams)
        inbounds = list(app._inbounds)
    finally:
        _restore(app, snapshot)

    try:
        # store=None: schema generation performs no persistence I/O, so a
        # surviving telemetry registration that declares persist= is rejected,
        # as it would be at runtime without a store.
        prepare_registrations(
            telemetry,
            devices,
            commands,
            settings,
            None,
            periodic=periodic,
            streams=streams,
            inbounds=inbounds,
        )
    except ValueError as exc:
        msg = (
            "settings resolution failed after expanding settings-derived "
            f"(ADR-023) name=/topic=/enabled= specs: {exc!r}"
        )
        raise SchemaBuildError(msg) from exc

    # Safety net: expansion should leave nothing behind.  A name_spec kind
    # expand_name_specs does not know would otherwise emit a phantom channel.
    check_names_expanded(itertools.chain(devices, telemetry, commands, inbounds))

    resolved = ResolvedApp(
        name=app.name,
        version=app.version,
        devices=tuple(devices),
        telemetry_registrations=tuple(telemetry),
        commands=tuple(commands),
        periodic_registrations=tuple(periodic),
        stream_registrations=tuple(streams),
        inbound_registrations=tuple(inbounds),
    )
    return resolved, settings.mqtt.topic_prefix or app.name


def resolve_app(
    app: App,
    env_file: str | Path | None = None,
    config_file: str | Path | None = None,
    *,
    _settings: Settings | None = None,
) -> tuple[ResolvedApp, str]:
    """Synchronous wrapper for CLI and non-async callers."""
    import asyncio

    return asyncio.run(
        resolve_app_async(app, env_file, config_file, _settings=_settings)
    )


async def resolved_asyncapi(
    app: App,
    *,
    env_file: str | Path | None = None,
    config_file: str | Path | None = None,
    topic_prefix: str | None = None,
) -> dict[str, Any]:
    """Return the AsyncAPI document of *app* with its settings resolved.

    The library form of ``cosalette schema dump --resolve-settings``: the
    returned dict is the document that command writes, before it is
    serialised to YAML or JSON.  Use it from tests or build scripts that
    already hold the App, instead of running the CLI and parsing its output.
    The CLI stays the primary interface (ADR-051).

    Settings are built from *env_file* and *config_file* as the CLI builds
    them.  Settings-derived (ADR-023) ``name=`` and ``topic=`` specs are then
    expanded and ``enabled=``-disabled registrations are dropped, so the
    document lists the entities the running app registers.

    The App is not modified: it is left exactly as it was, and its own
    :meth:`~cosalette.App.asyncapi` still describes the unresolved
    registrations.  Adapter factories and ``on_configure`` hooks do run, since
    resolution depends on them; adapters are resolved in dry-run mode and no
    adapter lifecycle or store is entered.  Each call returns a new dict.

    Args:
        app: The application to describe.
        env_file: A ``.env`` file to read Settings from (``--env-file``).
            ``None`` reads ``.env`` from the working directory if it exists,
            as pydantic-settings does.  An explicit path must exist.
        config_file: A TOML, YAML or JSON config file (``--config-file``).
            An explicit path must exist.
        topic_prefix: The MQTT topic prefix to compose channel addresses
            from (``--topic-prefix``).  ``None`` uses the resolved
            ``settings.mqtt.topic_prefix``, or the app name when that is
            empty (ADR-072).

    Returns:
        An AsyncAPI 3.0.0 document as a plain, JSON-serialisable dict.

    Raises:
        SettingsLoadError: When *env_file* or *config_file* is missing or
            cannot be parsed.
        pydantic.ValidationError: When the Settings are invalid, or
            *topic_prefix* is not a valid MQTT topic prefix.
        SchemaBuildError: When two registrations resolve to the same name, a
            ``persist=`` telemetry handler survives without a store, or a
            settings-derived name cannot be expanded.

    Example:
        >>> from cosalette.schema import resolved_asyncapi
        >>> doc = await resolved_asyncapi(app, env_file="prod.env")  # doctest: +SKIP
        >>> sorted(doc["channels"])  # doctest: +SKIP
    """
    resolved, prefix = await resolve_app_async(app, env_file, config_file)
    if topic_prefix is not None:
        from cosalette._settings import MqttSettings

        prefix = MqttSettings(topic_prefix=topic_prefix).topic_prefix
    return resolved.asyncapi(topic_prefix=prefix)


def resolved_asyncapi_sync(app: App, **kwargs: Any) -> dict[str, Any]:
    """Synchronous wrapper for scripts without a running event loop.

    Use :func:`resolved_asyncapi` when calling from async code.
    """
    import asyncio

    return asyncio.run(resolved_asyncapi(app, **kwargs))
