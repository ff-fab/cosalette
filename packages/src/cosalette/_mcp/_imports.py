"""Shared import utilities for MCP tool modules.

Provides validated import from ``module.path:attribute`` specifications
with caching.

Security Note:
    These tools use ``importlib.import_module()`` which executes module-level
    code at import time.  The ``isinstance``/``issubclass`` check runs *after*
    import, so a malicious ``app_spec``/``settings_spec`` could trigger
    side-effects before validation rejects it.

    **Mitigation:** The MCP server is local-only (stdio transport to a single
    IDE).  The caller already has code-execution capability in their own
    environment.  Network transports such as SSE are intentionally unsupported
    for this CLI because they would make these dynamic imports remotely
    reachable.
"""

from __future__ import annotations

import importlib
from typing import Any


def import_from_spec(spec: str) -> tuple[Any, str | None]:
    """Import an attribute from a ``module.path:attribute`` specification.

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

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return None, f"❌ Could not import module '{module_path}': {e}"
    except Exception as e:
        return None, f"❌ Error importing '{spec}': {e}"

    if not hasattr(module, attr_name):
        return None, f"❌ Module '{module_path}' has no attribute '{attr_name}'"

    return getattr(module, attr_name), None
