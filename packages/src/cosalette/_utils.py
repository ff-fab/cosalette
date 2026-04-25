"""General-purpose utilities for cosalette internals.

Helpers that don't belong to any specific domain module live here.
This keeps domain modules (context, app, mqtt, …) focused on their
core responsibility.
"""

from __future__ import annotations

import functools
import importlib
from typing import Any


def _import_string(dotted_path: str) -> Any:
    """Import an attribute from a ``module.path:attr_name`` string.

    Used for lazy adapter imports — hardware libraries may not be
    available on development machines (ADR-006 lazy import pattern).

    Args:
        dotted_path: Import path in ``module.path:attr_name`` format.

    Returns:
        The imported attribute (class, function, or other object).

    Raises:
        ImportError: If the module cannot be found.
        AttributeError: If the attribute doesn't exist in the module.
        ValueError: If the path doesn't contain exactly one ``:``.
    """
    parts = dotted_path.split(":")
    if len(parts) != 2:  # noqa: PLR2004
        msg = f"Expected 'module.path:attr_name', got {dotted_path!r}"
        raise ValueError(msg)

    module_path, attr_name = parts
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


def _callable_qualname(func: Any) -> str:
    """Return a qualified display name for any callable.

    Includes ``functools.partial`` support. Regular functions and methods
    expose ``__qualname__``.
    ``functools.partial`` does not copy that attribute from its wrapped
    callable, so direct access raises ``AttributeError``.  This helper
    unwraps partials recursively and prefixes the result with
    ``partial(…)`` so error messages and log output remain meaningful.

    Examples::

        def my_handler(): ...
        _callable_qualname(my_handler)
        # → "my_handler"

        import functools
        _callable_qualname(functools.partial(my_handler, 1))
        # → "partial(my_handler)"

    Args:
        func: Any callable — regular function, method, partial, or callable object.

    Returns:
        A human-readable qualified name string that never raises.
    """
    if (qualname := getattr(func, "__qualname__", None)) is not None:
        return qualname
    if isinstance(func, functools.partial):
        return f"partial({_callable_qualname(func.func)})"
    return type(func).__qualname__


def _callable_name(func: Any) -> str:
    """Return a short display name for any callable, including ``functools.partial``.

    Unlike :func:`_callable_qualname`, this targets ``__name__`` (the simple
    identifier, without enclosing class path), which is appropriate for MQTT
    topic segment derivation.  For partials the inner callable's name is
    returned without the ``partial(…)`` wrapper so that implicit topic names
    stay clean.

    Examples::

        def my_handler(): ...
        _callable_name(my_handler)
        # → "my_handler"

        import functools
        _callable_name(functools.partial(my_handler, 1))
        # → "my_handler"

    Args:
        func: Any callable — regular function, method, partial, or callable object.

    Returns:
        A short name string that never raises.
    """
    if (name := getattr(func, "__name__", None)) is not None:
        return name
    if isinstance(func, functools.partial):
        return _callable_name(func.func)
    return type(func).__name__
