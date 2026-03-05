"""General-purpose utilities for cosalette internals.

Helpers that don't belong to any specific domain module live here.
This keeps domain modules (context, app, mqtt, …) focused on their
core responsibility.
"""

from __future__ import annotations

import importlib
from typing import Any


def _import_string(dotted_path: str) -> Any:
    """Import a class from a ``module.path:ClassName`` string.

    Used for lazy adapter imports — hardware libraries may not be
    available on development machines (ADR-006 lazy import pattern).

    Args:
        dotted_path: Import path in ``module.path:ClassName`` format.

    Returns:
        The imported class/object.

    Raises:
        ImportError: If the module cannot be found.
        AttributeError: If the class doesn't exist in the module.
        ValueError: If the path doesn't contain exactly one ``:``.
    """
    parts = dotted_path.split(":")
    if len(parts) != 2:  # noqa: PLR2004
        msg = f"Expected 'module.path:ClassName', got {dotted_path!r}"
        raise ValueError(msg)

    module_path, class_name = parts
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
