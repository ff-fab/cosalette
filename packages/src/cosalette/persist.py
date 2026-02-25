"""Public save-policy utilities — ``from cosalette.persist import SaveOnPublish``."""

from __future__ import annotations

from cosalette._persist import (
    AllSavePolicy,
    AnySavePolicy,
    PersistPolicy,
    SaveOnChange,
    SaveOnPublish,
    SaveOnShutdown,
)

__all__ = [
    "AllSavePolicy",
    "AnySavePolicy",
    "PersistPolicy",
    "SaveOnChange",
    "SaveOnPublish",
    "SaveOnShutdown",
]
