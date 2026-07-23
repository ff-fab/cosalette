"""Shared import utilities for MCP tool modules.

Provides validated import from ``module.path:attribute`` specifications
with an import allowlist.

Security:
    ``importlib.import_module()`` executes the target module's top-level code
    at import time, and the ``isinstance``/``issubclass`` check only runs
    *afterwards* — so importing an untrusted spec is code execution, not just
    introspection. A prompt-injected coding agent driving the MCP tools could
    otherwise call e.g. ``cosalette_inspect_app("evil_module:app")`` and run
    arbitrary code as the developer.

    MCP tool imports are therefore gated by an allowlist read from the
    ``COSALETTE_MCP_IMPORT_ALLOW`` environment variable — a comma-separated
    list of module prefixes, matched boundary-aware (``"myapp"`` allows
    ``myapp`` and ``myapp.sub`` but not ``myapp_evil``). When the variable is
    unset or empty, **all** gated imports are refused. Set it to your app's
    module prefix(es) to enable introspection.

    The MCP server is stdio-only; network transports (e.g. SSE) are
    intentionally unsupported because they would make these dynamic imports
    remotely reachable.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

ALLOW_ENV = "COSALETTE_MCP_IMPORT_ALLOW"


def _allowed_prefixes() -> list[str]:
    """Return the module prefixes permitted by ``COSALETTE_MCP_IMPORT_ALLOW``."""
    raw = os.environ.get(ALLOW_ENV, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _import_allowed(module_path: str, prefixes: list[str]) -> bool:
    """Return True if *module_path* is covered by an allowlist prefix.

    Boundary-aware: prefix ``"myapp"`` matches ``"myapp"`` and ``"myapp.sub"``
    but not ``"myapp_evil"``.
    """
    return any(
        module_path == prefix or module_path.startswith(prefix + ".")
        for prefix in prefixes
    )


def _check_allowlist(module_path: str, prefixes: list[str]) -> str | None:
    """Return an error string if *module_path* is not permitted, else ``None``.

    Pure function — reads no global state.
    """
    if not prefixes:
        return (
            f"❌ Refusing to import '{module_path}': importing a module executes "
            f"its top-level code. Set {ALLOW_ENV} to your app's module prefix(es) "
            f"(comma-separated) to allow introspection, e.g. {ALLOW_ENV}=myapp"
        )
    if not _import_allowed(module_path, prefixes):
        return (
            f"❌ Refusing to import '{module_path}': not covered by {ALLOW_ENV} "
            f"({', '.join(prefixes)}). Add its module prefix to {ALLOW_ENV} if you "
            f"trust it."
        )
    return None


def _import_from_spec_unchecked(spec: str) -> tuple[Any, str | None]:
    """Import an attribute from *spec* without allowlist enforcement.

    **Developer CLI use only.** This path is intentionally free of the
    MCP allowlist because the CLI treats ``module:app`` as a documented
    trust boundary (analogous to ``uvicorn module:app``) — not a remotely
    reachable input. Do not call from MCP tools.
    """
    spec = spec.strip()
    if ":" not in spec:
        return (
            None,
            f"❌ Invalid spec '{spec}'. Expected format: 'module.path:attribute'",
        )
    module_path, attr_name = spec.rsplit(":", 1)
    module_path = module_path.strip()
    attr_name = attr_name.strip()
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return None, f"❌ Could not import module '{module_path}': {e}"
    except Exception as e:
        return None, f"❌ Error importing '{spec}': {e}"
    if not hasattr(module, attr_name):
        return None, f"❌ Module '{module_path}' has no attribute '{attr_name}'"
    return getattr(module, attr_name), None


def import_from_spec(
    spec: str,
) -> tuple[Any, str | None]:
    """Import an attribute from a ``module.path:attribute`` specification.

    Importing executes the target module's top-level code. The module must
    be permitted by ``COSALETTE_MCP_IMPORT_ALLOW`` or the import is refused
    *before* any module code runs.

    For developer-invoked CLIs where the ``module:app`` spec is a documented
    trust boundary, use :func:`_import_from_spec_unchecked` instead.

    Returns:
        ``(attribute, None)`` on success, ``(None, error_message)`` on failure.
    """
    spec = spec.strip()
    if ":" not in spec:
        return (
            None,
            f"❌ Invalid spec '{spec}'. Expected format: 'module.path:attribute'",
        )

    module_path, attr_name = spec.rsplit(":", 1)
    module_path = module_path.strip()
    attr_name = attr_name.strip()

    prefixes = _allowed_prefixes()
    allow_err = _check_allowlist(module_path, prefixes)
    if allow_err is not None:
        return None, allow_err

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return None, f"❌ Could not import module '{module_path}': {e}"
    except Exception as e:
        return None, f"❌ Error importing '{spec}': {e}"

    if not hasattr(module, attr_name):
        return None, f"❌ Module '{module_path}' has no attribute '{attr_name}'"

    return getattr(module, attr_name), None
