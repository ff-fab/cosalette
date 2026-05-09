"""Runtime validation and serialisation backend for typed handler contracts.

Uses Pydantic v2 :class:`~pydantic.TypeAdapter` as the uniform engine for:

- JSON payload parsing and validation (inbound MQTT string → typed Python object).
- Return value normalisation (typed Python value → JSON-compatible ``dict``
  for the MQTT publish path).

:class:`~pydantic.TypeAdapter` instances are cached per annotation via a
thread-safe double-checked locking registry so that per-message overhead is
dominated by JSON decode time, not schema compilation.

Supported first-wave kinds:
    ``BaseModel``, stdlib ``dataclass``, ``TypedDict``, primitives
    (``str``, ``int``, ``float``, ``bool``), generic mappings/sequences,
    and JSON-compatible ``dict``/``list``.

See Also:
    ADR-046 — Typed Handler Contract Validation.
"""

from __future__ import annotations

import threading
from typing import Any

from pydantic import TypeAdapter, ValidationError

from cosalette._json import JSONDecodeError, loads

# ---------------------------------------------------------------------------
# TypeAdapter cache — thread-safe, keyed by annotation identity
# ---------------------------------------------------------------------------

_adapter_lock = threading.Lock()
_adapter_cache: dict[Any, TypeAdapter[Any]] = {}


def _get_adapter(annotation: Any) -> TypeAdapter[Any]:
    """Return a cached :class:`~pydantic.TypeAdapter` for *annotation*.

    Thread-safe double-checked locking: reads bypass the lock when the
    adapter is already cached.  Unhashable annotations (rare in practice)
    bypass the cache and construct a fresh adapter.
    """
    try:
        if annotation in _adapter_cache:
            return _adapter_cache[annotation]
    except TypeError:
        pass  # unhashable annotation — skip cache lookup

    with _adapter_lock:
        try:
            if annotation not in _adapter_cache:
                _adapter_cache[annotation] = TypeAdapter(annotation)
            return _adapter_cache[annotation]
        except TypeError:
            # Unhashable type — build without caching (extremely rare)
            return TypeAdapter(annotation)


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class PayloadValidationError(Exception):
    """Raised when an inbound MQTT payload fails schema validation.

    Provides handler and parameter context for actionable diagnostics.

    Attributes:
        param: Parameter name that failed validation, or ``None``.
        handler: Qualified name of the handler, or ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        param: str | None = None,
        handler: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.param = param
        self.handler = handler
        if cause is not None:
            self.__cause__ = cause


class ReturnValidationError(Exception):
    """Raised when a handler return value fails normalisation.

    Attributes:
        handler: Qualified name of the handler, or ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        handler: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.handler = handler
        if cause is not None:
            self.__cause__ = cause


# ---------------------------------------------------------------------------
# Parsing and normalisation
# ---------------------------------------------------------------------------


def _error_context(param: str | None, handler: str | None) -> str:
    """Build the trailing context fragment for :class:`PayloadValidationError`."""
    param_ctx = f" (parameter '{param}')" if param else ""
    handler_ctx = f" in handler {handler!r}" if handler else ""
    return param_ctx + handler_ctx


def _decode_json(raw: str, param: str | None, handler: str | None) -> Any:
    """JSON-decode *raw*, raising :class:`PayloadValidationError` on failure."""
    try:
        return loads(raw)
    except (JSONDecodeError, ValueError) as exc:
        ctx = _error_context(param, handler)
        raise PayloadValidationError(
            f"MQTT payload is not valid JSON{ctx}: {exc}",
            param=param,
            handler=handler,
            cause=exc,
        ) from exc


def _validate_python_value(
    python_value: Any,
    annotation: Any,
    param: str | None,
    handler: str | None,
) -> Any:
    """Validate *python_value* via :class:`~pydantic.TypeAdapter`, raising on failure.

    Raises :class:`PayloadValidationError` on Pydantic validation errors.
    """
    adapter = _get_adapter(annotation)
    try:
        return adapter.validate_python(python_value)
    except ValidationError as exc:
        ctx = _error_context(param, handler)
        raise PayloadValidationError(
            f"Payload validation failed{ctx}: {exc}",
            param=param,
            handler=handler,
            cause=exc,
        ) from exc


def parse_payload(
    raw: str | None,
    annotation: Any,
    *,
    param: str | None = None,
    handler: str | None = None,
) -> Any:
    """Parse and validate a raw MQTT payload string against *annotation*.

    Behaviour by annotation:

    - ``str`` → returns *raw* unchanged (empty string when *raw* is ``None``).
    - Any other type → JSON-decodes *raw*, then validates via
      :class:`~pydantic.TypeAdapter`.
    - ``None`` or empty *raw* → passes Python ``None`` to the adapter,
      allowing ``T | None`` optional types to succeed on scheduled
      (no-payload) runs.

    Args:
        raw: Raw MQTT payload string, or ``None`` for scheduled runs.
        annotation: Target Python type — ``BaseModel``, dataclass,
            ``TypedDict``, primitive, or generic container.
        param: Parameter name for error messages.
        handler: Handler qualified name for error messages.

    Returns:
        Validated Python value matching *annotation*.

    Raises:
        PayloadValidationError: On JSON decode error or validation failure.
    """
    if annotation is str:
        return raw or ""

    python_value = _decode_json(raw, param, handler) if raw else None
    return _validate_python_value(python_value, annotation, param, handler)


def normalize_return(
    value: Any,
    annotation: Any,
    *,
    handler: str | None = None,
) -> dict[str, Any] | None:
    """Normalise a handler return value to a JSON-compatible ``dict``.

    Rules applied in order:

    1. ``None`` → returns ``None`` (suppresses publish).
    2. *annotation* present → serialises via
       ``TypeAdapter(annotation).dump_python(mode='json')``.
    3. No annotation → uses *value* as-is.
    4. Normalised ``dict`` → published as-is.
    5. Normalised primitive (``int``, ``float``, ``bool``, ``str``) or
       ``list`` → wrapped as ``{"value": <normalised>}`` so the existing
       ``publish_state`` dict contract remains intact.

    Args:
        value: The handler return value.
        annotation: Return type annotation, or ``None`` when absent.
        handler: Handler qualified name for error messages.

    Returns:
        A ``dict`` ready for ``publish_state()``, or ``None`` to suppress.

    Raises:
        ReturnValidationError: If ``TypeAdapter`` serialisation fails.
    """
    if value is None:
        return None

    normalised: Any
    if annotation is not None and annotation is not type(None):
        try:
            adapter = _get_adapter(annotation)
            validated = adapter.validate_python(value)
            normalised = adapter.dump_python(validated, mode="json")
        except Exception as exc:
            handler_ctx = f" in handler {handler!r}" if handler else ""
            raise ReturnValidationError(
                f"Return value serialisation failed{handler_ctx}: {exc}",
                handler=handler,
                cause=exc,
            ) from exc
    else:
        normalised = value

    if normalised is None:
        return None
    if isinstance(normalised, dict):
        return normalised  # type: ignore[return-value]
    # Wrap primitives and lists so publish_state dict contract is preserved
    return {"value": normalised}
