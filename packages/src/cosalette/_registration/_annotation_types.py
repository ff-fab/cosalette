"""Resolve registration annotation types only when runtime consumers inspect them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cosalette._utils import _import_string

if TYPE_CHECKING:
    from cosalette._cron import CronSchedule as CronSchedule
    from cosalette._strategies import PublishStrategy as PublishStrategy

_EXPORTS = {
    "CronSchedule": "cosalette._cron:CronSchedule",
    "PublishStrategy": "cosalette._strategies:PublishStrategy",
}


def __getattr__(name: str) -> Any:
    """Load the canonical type on first annotation evaluation."""
    if name not in _EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    value = _import_string(_EXPORTS[name])
    globals()[name] = value
    return value
