"""PEP 562 lazy re-exports: import a submodule only when one of its names is used.

Keeps ``import cosalette`` cheap for processes that need little of the framework,
such as the ``health`` probe that a container runtime starts on every check.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Mapping


def lazy_exports(
    package: str, exports: Mapping[str, tuple[str, ...]]
) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """Return ``(__getattr__, __dir__)`` for *package*, given ``{module: names}``."""
    namespace = vars(sys.modules[package])
    origin = {name: module for module, names in exports.items() for name in names}

    def __getattr__(name: str) -> object:
        try:
            module = origin[name]
        except KeyError:
            msg = f"module {package!r} has no attribute {name!r}"
            raise AttributeError(msg) from None
        value = getattr(importlib.import_module(module), name)
        namespace[name] = value  # cache: later lookups skip __getattr__
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *origin, *namespace.get("__all__", ())})

    return __getattr__, __dir__
