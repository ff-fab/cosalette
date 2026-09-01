"""Shared test helpers for EntityNotifier-based tests."""

from __future__ import annotations

import dataclasses

from cosalette import EntityNotifier


@dataclasses.dataclass
class _NotifierHolder:
    """Lifespan-scoped state that stores the injected notifier."""

    notify: EntityNotifier
