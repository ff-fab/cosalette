"""Compatibility stub: _reactors moved to cosalette._wiring._reactors.

Deprecated: import from cosalette._wiring._reactors directly.
This stub will be removed in a follow-up cleanup PR.
"""

# Re-exported for backward compatibility
from cosalette._wiring._reactors import dispatch_reactors, run_reactor_boundaries

__all__ = ["dispatch_reactors", "run_reactor_boundaries"]
