from __future__ import annotations

import os
from pathlib import Path


def _resolve_default_store_path(app_name: str) -> Path:
    """Derive the default persistence store path for *app_name*.

    Resolution precedence:
    1. ``<APP_NAME_UPPER>_STORE_PATH`` environment variable (operator override).
    2. ``$XDG_STATE_HOME/<app_name>/store.json``.
    3. ``~/.local/state/<app_name>/store.json`` (XDG default).

    ``<APP_NAME_UPPER>`` upper-cases the name and replaces hyphens and spaces
    with underscores (``caldates2mqtt`` -> ``CALDATES2MQTT``). The returned path
    is not guaranteed to exist; ``JsonFileStore`` creates parent directories on
    first save. See ADR-049.
    """
    safe_name = app_name.upper().replace("-", "_").replace(" ", "_")
    explicit = os.environ.get(f"{safe_name}_STORE_PATH")
    if explicit:
        return Path(explicit)
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / app_name / "store.json"
