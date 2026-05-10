"""Compatibility stub: _adapter_lifecycle moved to cosalette._wiring._adapter_lifecycle.

Deprecated: import from cosalette._wiring._adapter_lifecycle directly.
This stub will be removed in a follow-up cleanup PR.
"""

# Re-exported for backward compatibility
from cosalette._wiring._adapter_lifecycle import (
    _AdapterEntry,
    _is_async_context_manager,
    detect_health_checkable,
    detect_restartable_adapters,
    enter_adapter_or_abort,
    enter_lifecycle_adapters,
    enter_restartable_adapters,
    enter_single_adapter,
    exit_single_adapter,
    resolve_adapters,
    restart_single_adapter,
)

__all__ = [
    "_AdapterEntry",
    "_is_async_context_manager",
    "detect_health_checkable",
    "detect_restartable_adapters",
    "enter_adapter_or_abort",
    "enter_lifecycle_adapters",
    "enter_restartable_adapters",
    "enter_single_adapter",
    "exit_single_adapter",
    "resolve_adapters",
    "restart_single_adapter",
]
