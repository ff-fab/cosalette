from __future__ import annotations

import os
from pathlib import Path


def _resolve_default_store_path(app_name: str) -> Path:
    """Derive the default persistence store path for *app_name*.

    Resolution precedence:
    1. ``<NAME>_STORE_PATH`` environment variable (operator override).
    2. ``$XDG_STATE_HOME/<app_name>/store.json``.
    3. ``~/.local/state/<app_name>/store.json`` (XDG default).

    ``<NAME>`` is the app name upper-cased with hyphens and spaces replaced
    by underscores (e.g. ``my-app`` -> ``MY_APP_STORE_PATH``). The returned
    path is not guaranteed to exist; ``JsonFileStore`` creates parent
    directories on first save. See ADR-049.

    Raises:
        ValueError: If *app_name* contains a path-traversal segment (``"."``
            or ``".."``).  Use the ``<NAME>_STORE_PATH`` env var for unusual
            app names.
    """
    safe_name = app_name.upper().replace("-", "_").replace(" ", "_")
    explicit = os.environ.get(f"{safe_name}_STORE_PATH")
    if explicit:
        return Path(explicit)

    # Guard against path-traversal before using app_name as a directory segment.
    # validate_mqtt_name() rejects "/" so app_name is always a single path
    # component; the only dangerous values are "." (collapses to base dir) and
    # ".." (escapes base dir).  Note: Path(".").parts == () in Python, so we
    # compare the raw string rather than relying on pathlib parts.
    if app_name in {".", ".."}:
        msg = (
            f"App name {app_name!r} contains a path-traversal segment and cannot "
            f"be used to derive a default store path. "
            f"Set {safe_name}_STORE_PATH to an explicit path."
        )
        raise ValueError(msg)

    xdg_state = os.environ.get("XDG_STATE_HOME")
    # Per the XDG Base Directory Specification, XDG_STATE_HOME must be an
    # absolute path; relative or empty values are silently ignored.
    if xdg_state and Path(xdg_state).is_absolute():
        base = Path(xdg_state)
    else:
        base = Path.home() / ".local" / "state"
    return base / app_name / "store.json"
