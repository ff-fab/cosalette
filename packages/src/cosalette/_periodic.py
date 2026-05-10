"""Compatibility stub: _periodic moved to cosalette._runners._periodic.

Deprecated: import from cosalette._runners._periodic directly.
This stub will be removed in a follow-up cleanup PR.
"""

# Re-exported for backward compatibility
from cosalette._runners._periodic import _PeriodicRegistration, run_periodic

__all__ = ["_PeriodicRegistration", "run_periodic"]
