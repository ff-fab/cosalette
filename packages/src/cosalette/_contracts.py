"""Compatibility stub: _contracts moved to cosalette._runners._contracts.

Deprecated: import from cosalette._runners._contracts directly.
This stub will be removed in a follow-up cleanup PR.
"""

# Re-exported for backward compatibility
from cosalette._runners._contracts import (
    PayloadValidationError,
    ReturnValidationError,
    get_return_annotation,
    normalize_handler_return,
    normalize_return,
    parse_payload,
)

__all__ = [
    "PayloadValidationError",
    "ReturnValidationError",
    "get_return_annotation",
    "normalize_handler_return",
    "normalize_return",
    "parse_payload",
]
