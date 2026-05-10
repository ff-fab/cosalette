"""AsyncAPI mixin for the App class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cosalette._app import App


class _AsyncapiMixin:
    """Adds :meth:`asyncapi` to :class:`~cosalette.App`."""

    def asyncapi(self: App) -> dict[str, Any]:
        """Return a canonical AsyncAPI 3.0.0 document dict for this application.

        The document describes all registered devices, telemetry, and commands
        as AsyncAPI channels and operations, with typed JSON Schema payloads
        inferred from explicit ``payload_model``/``state_model`` decorator
        arguments (which take precedence) or from handler return-type annotations.

        The returned dict is JSON-serialisable and deterministic (channels,
        operations, and component schemas are sorted alphabetically).

        An ``x-cosalette-contract-version`` key in the ``info`` section tracks
        the contract-shape version independently from the application version.

        The result is cached on the instance; registrations are immutable
        after app setup so subsequent calls return the same object.

        Returns:
            A plain ``dict`` conforming to AsyncAPI 3.0.0.
        """
        cached: dict[str, Any] | None = getattr(self, "_asyncapi_cache", None)
        if cached is not None:
            return cached

        from cosalette._schema._asyncapi import build_app_asyncapi

        result = build_app_asyncapi(self)
        object.__setattr__(self, "_asyncapi_cache", result)
        return result
