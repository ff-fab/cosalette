"""AsyncAPI mixin for the App class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cosalette._app import App


class _AsyncapiMixin:
    """Adds :meth:`asyncapi` to :class:`~cosalette.App`."""

    def asyncapi(self: App, *, topic_prefix: str | None = None) -> dict[str, Any]:
        """Return a canonical AsyncAPI 3.0.0 document dict for this application.

        The document describes all registered devices, telemetry, and commands
        as AsyncAPI channels and operations, with typed JSON Schema payloads
        inferred from explicit ``payload_model``/``state_model`` decorator
        arguments (which take precedence) or from handler return-type annotations.

        The returned dict is JSON-serialisable and deterministic (channels,
        operations, and component schemas are sorted alphabetically).

        An ``x-cosalette-contract-version`` key in the ``info`` section tracks
        the contract-shape version independently from the application version.

        Results are cached per resolved topic prefix; registrations are immutable
        after app setup so a repeated call with the same prefix returns the same
        object.  Two different prefixes never share an entry, since the prefix
        changes every ``channel.address`` in the document (ADR-072).

        Args:
            topic_prefix: The resolved MQTT topic prefix
                (``settings.mqtt.topic_prefix or app.name``) that channel
                addresses are composed from.  ``None`` (the default) means
                "unresolved" and falls back to :attr:`name`, which is what the
                document described before ADR-072.  The app's *identity*
                (``x-cosalette-app``) is always :attr:`name` regardless.

        Returns:
            A plain ``dict`` conforming to AsyncAPI 3.0.0.
        """
        prefix = topic_prefix or self.name
        cache: dict[str, dict[str, Any]] | None = getattr(self, "_asyncapi_cache", None)
        if cache is None:
            cache = {}
            object.__setattr__(self, "_asyncapi_cache", cache)
        elif prefix in cache:
            return cache[prefix]

        from cosalette._schema._asyncapi import build_app_asyncapi

        result = build_app_asyncapi(self, topic_prefix=prefix)
        cache[prefix] = result
        return result
