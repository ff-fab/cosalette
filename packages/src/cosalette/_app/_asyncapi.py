"""AsyncAPI mixin for the App class."""

from __future__ import annotations

from typing import Any


class _AsyncapiMixin:
    """Adds :meth:`asyncapi` to :class:`~cosalette.App`."""

    def asyncapi(self) -> dict[str, Any]:
        """Return a canonical AsyncAPI 3.0.0 document dict for this application.

        The document describes all registered devices, telemetry, and commands
        as AsyncAPI channels and operations, with typed JSON Schema payloads
        inferred from explicit ``payload_model``/``state_model`` decorator
        arguments (which take precedence) or from handler return-type annotations.

        The returned dict is JSON-serialisable and deterministic (channels,
        operations, and component schemas are sorted alphabetically).

        An ``x-cosalette-contract-version`` key in the ``info`` section tracks
        the contract-shape version independently from the application version.

        Returns:
            A plain ``dict`` conforming to AsyncAPI 3.0.0.
        """
        from cosalette._schema._asyncapi import build_app_asyncapi

        return build_app_asyncapi(self)  # ty: ignore[invalid-argument-type]
