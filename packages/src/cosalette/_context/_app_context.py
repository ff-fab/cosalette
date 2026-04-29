"""AppContext for application lifespan.

Context for the application lifespan provided to lifespan async context managers.
"""

from __future__ import annotations

from typing import cast

from cosalette._settings import Settings


class AppContext:
    """Context for the application lifespan.

    Provided to the lifespan async context manager registered via
    ``App(lifespan=...)``.  Offers access to settings and adapter
    resolution but NOT per-device features (no publish, no on_command,
    no sleep).

    See Also:
        ADR-001 — Framework architecture (lifespan).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        adapters: dict[type, object],
    ) -> None:
        """Initialise lifecycle-hook context.

        Args:
            settings: Application settings instance.
            adapters: Resolved adapter registry mapping port types to instances.
        """
        self._settings = settings
        self._adapters = adapters

    @property
    def settings(self) -> Settings:
        """Application settings instance."""
        return self._settings

    def adapter[T](self, port_type: type[T]) -> T:
        """Resolve an adapter by port type.

        Args:
            port_type: The Protocol type to look up.

        Returns:
            The adapter instance registered for that port type.

        Raises:
            LookupError: If no adapter is registered for the port type.
        """
        try:
            return cast(T, self._adapters[port_type])
        except KeyError:
            msg = f"No adapter registered for {port_type!r}"
            raise LookupError(msg) from None
