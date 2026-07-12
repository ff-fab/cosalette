from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from cosalette._persistence._stores import JsonFileStore, Store

_ENV_NAME_RE = re.compile(r"[^A-Z0-9_]")


def _normalize_env_name(app_name: str) -> str:
    """Normalize *app_name* to a shell-safe ``<NAME>_STORE_PATH`` stem.

    Upper-cases and replaces every non-alphanumeric character with ``_``
    (e.g. ``my-app`` -> ``MY_APP``, ``sensor.hub`` -> ``SENSOR_HUB``).
    """
    return _ENV_NAME_RE.sub("_", app_name.upper())


def _resolve_default_store_path(app_name: str) -> Path:
    """Derive the default persistence store path for *app_name*.

    Resolution precedence:
    1. ``<NAME>_STORE_PATH`` environment variable (operator override).
    2. ``$XDG_STATE_HOME/<app_name>/store.json``.
    3. ``~/.local/state/<app_name>/store.json`` (XDG default).

    ``<NAME>`` is the app name upper-cased with non-alphanumeric characters
    replaced by underscores (e.g. ``my-app`` -> ``MY_APP_STORE_PATH``). The
    returned path is not guaranteed to exist; ``JsonFileStore`` creates parent
    directories on first save. See ADR-049.

    Raises:
        ValueError: If *app_name* contains a path-traversal segment (``"."``
            or ``".."``).  Use the ``<NAME>_STORE_PATH`` env var for unusual
            app names.
    """
    safe_name = _normalize_env_name(app_name)
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


# ---------------------------------------------------------------------------
# Configurable default store backend (Task 2)
# ---------------------------------------------------------------------------

_DEFAULT_STORE_FACTORY: Callable[[Path], Store] = JsonFileStore


def set_default_store_backend(factory: Callable[[Path], Store] | None) -> None:
    """Set the process-wide factory for auto-resolved default stores.

    When ``store=`` is omitted from ``App(...)``, the default store is created
    as ``factory(path)``.  Defaults to :class:`JsonFileStore`.  Pass any class
    or callable taking a :class:`~pathlib.Path` and returning a :class:`Store`
    (e.g. ``SqliteStore``).  Pass ``None`` to reset to the built-in default.

    Process-global and not thread-safe: call once at import/startup before
    constructing any ``App``.  Explicit ``store=`` arguments are unaffected.
    """
    global _DEFAULT_STORE_FACTORY
    _DEFAULT_STORE_FACTORY = factory if factory is not None else JsonFileStore


def _create_default_store(path: Path) -> Store:
    """Create the default store at *path* using the configured backend."""
    return _DEFAULT_STORE_FACTORY(path)


# ---------------------------------------------------------------------------
# Ephemeral-store detection helpers (Task 3)
# ---------------------------------------------------------------------------


def _in_container() -> bool:
    """Best-effort container-runtime detection (Docker/Podman/systemd)."""
    return (
        Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
        or bool(os.environ.get("container"))  # noqa: SIM112 systemd uses lowercase
    )


def _default_store_is_ephemeral(app_name: str) -> bool:
    """True when the auto-resolved default store path is likely ephemeral.

    Durable when the operator set ``<NAME>_STORE_PATH``; otherwise it lives
    under XDG_STATE_HOME/home, which is ephemeral inside a container.
    """
    if os.environ.get(f"{_normalize_env_name(app_name)}_STORE_PATH"):
        return False
    return _in_container()
