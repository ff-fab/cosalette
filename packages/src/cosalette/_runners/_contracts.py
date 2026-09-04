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

import functools
import threading
import types
import typing
from typing import Any

from pydantic import TypeAdapter, ValidationError

from cosalette._json import JSONDecodeError, loads

# ---------------------------------------------------------------------------
# TypeAdapter cache — thread-safe, keyed by annotation identity
# ---------------------------------------------------------------------------

_adapter_lock = threading.Lock()
_adapter_cache: dict[Any, TypeAdapter[Any]] = {}
_thread_local = threading.local()


def _get_adapter(annotation: Any) -> TypeAdapter[Any]:
    """Return a cached :class:`~pydantic.TypeAdapter` for *annotation*.

    Uses a two-level cache:

    1. A per-thread L1 cache (``threading.local``) for lock-free reads —
       safe under both CPython GIL and Python 3.13 free-threaded mode
       (PEP 703) because each thread owns its local dict exclusively.
    2. A global L2 ``_adapter_cache`` guarded by ``_adapter_lock`` for
       cross-thread sharing.

    Unhashable annotations bypass both caches and construct a fresh adapter.
    """
    # L1: per-thread cache — no lock needed, thread-local dict is private
    l1: dict[Any, TypeAdapter[Any]] | None = getattr(_thread_local, "adapters", None)
    if l1 is None:
        _thread_local.adapters = {}
        l1 = _thread_local.adapters

    try:
        if annotation in l1:
            return l1[annotation]
    except TypeError:
        # Unhashable annotation — skip both caches
        return TypeAdapter(annotation)

    # L2: global cache with lock
    with _adapter_lock:
        try:
            if annotation not in _adapter_cache:
                _adapter_cache[annotation] = TypeAdapter(annotation)
            adapter = _adapter_cache[annotation]
        except TypeError:
            return TypeAdapter(annotation)

    # Populate L1 from L2
    l1[annotation] = adapter
    return adapter


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


def _safe_error_summary(exc: ValidationError) -> str:
    """Summarise *exc* using only framework-owned data.

    Sanitize: build error text ONLY from framework-owned data (field
    location codes + Pydantic error type codes) — never from ``'msg'``
    text, which can embed the rejected value when custom validators
    raise ``ValueError(f"bad: {value}")``.  Publishing ``msg`` verbatim
    would echo user-controlled data to every MQTT error-topic subscriber
    (OWASP A03 — Injection).
    """
    return "; ".join(
        f"{'.'.join(str(loc_part) for loc_part in e['loc'])}: type={e['type']}"
        for e in exc.errors(include_url=False, include_input=False)
    )


def _annotation_label(annotation: Any) -> str:
    """Return a short, human-readable name for *annotation*.

    Falls back to ``repr`` for generic aliases and unions, which have no
    ``__name__``.
    """
    name = getattr(annotation, "__name__", None)
    return name if isinstance(name, str) else repr(annotation)


def _validate_python_value(
    python_value: Any,
    annotation: Any,
    param: str | None,
    handler: str | None,
) -> Any:
    """Validate *python_value* via :class:`~pydantic.TypeAdapter`, raising on failure.

    Raises :class:`PayloadValidationError` on Pydantic validation errors.
    Error messages are sanitized to exclude raw input values (OWASP A03).
    """
    adapter = _get_adapter(annotation)
    try:
        return adapter.validate_python(python_value)
    except ValidationError as exc:
        ctx = _error_context(param, handler)
        raise PayloadValidationError(
            f"Payload validation failed{ctx}: {_safe_error_summary(exc)}",
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

    python_value = _decode_json(raw, param, handler) if raw is not None else None
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
       ``TypeAdapter(annotation).dump_python(mode='json', warnings='error')``.
       A value that does not conform is re-run through ``validate_python``
       and rejected if it still does not match (ADR-068 clause B), so a
       non-conforming plain ``dict`` can never reach the state topic.
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
        ReturnValidationError: If *value* does not conform to *annotation*,
            or if ``TypeAdapter`` serialisation fails.
    """
    if value is None:
        return None

    normalised: Any
    if annotation is not None and annotation is not types.NoneType:
        try:
            adapter = _get_adapter(annotation)
            # EAFP fast path: attempt dump_python directly on the value.
            # This is free for already-valid instances (BaseModel, dataclass,
            # TypedDict) regardless of whether the annotation is a concrete
            # type or a generic alias (list[int], dict[str, T], X | None,
            # Annotated[…]) — the isinstance fast-path only covered concrete
            # types and missed all PEP 585/604 generics.  When the value is
            # not already valid, Pydantic raises an exception and we fall back
            # to validate_python to coerce/validate before dumping.
            #
            # ADR-068 clause B: warnings="error" promotes
            # PydanticSerializationUnexpectedValue to
            # PydanticSerializationError, so a non-conforming plain dict falls
            # through to validate_python instead of being republished verbatim.
            # Clause G: available across the existing pydantic>=2.12.5,<3 pin;
            # no version bump needed.
            try:
                normalised = adapter.dump_python(value, mode="json", warnings="error")
            except Exception:
                validated = adapter.validate_python(value)
                normalised = adapter.dump_python(validated, mode="json")
        except Exception as exc:
            handler_ctx = f" in handler {handler!r}" if handler else ""
            # Sanitize: do not include the return value in the error message.
            exc_type = type(exc).__name__
            raise ReturnValidationError(
                f"Return value serialisation failed{handler_ctx}: {exc_type}",
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


def validate_state_payload(
    payload: dict[str, object],
    state_model: Any,
    *,
    handler: str | None = None,
) -> dict[str, object]:
    """Validate and normalise a ``publish_state()`` payload against *state_model*.

    Unlike :func:`normalize_return` — which serialises an already-typed
    handler *return value* and therefore takes an EAFP ``dump_python``
    fast path — this function always ``validate_python``s first.  The
    caller supplies a plain ``dict`` assembled by hand, so
    ``dump_python`` alone would let a non-conforming dict through with
    nothing but a Pydantic serializer warning.  Validating first is what
    makes ``state_model`` load-bearing for
    :meth:`~cosalette.DeviceContext.publish_state`.

    Args:
        payload: The dict handed to ``publish_state()``.
        state_model: The declared state model (any type
            :class:`~pydantic.TypeAdapter` accepts).
        handler: Qualified handler name for error messages.

    Returns:
        The validated payload dumped to JSON-compatible form.  Non-mapping
        results are wrapped as ``{"value": ...}``, mirroring
        :func:`normalize_return`.

    Raises:
        ReturnValidationError: If *payload* does not conform to
            *state_model*, or if serialisation of the validated value
            fails.  The message names the offending field paths, the
            model, and the handler.

    See Also:
        ADR-046 — ``state_model`` drives runtime validation.
        ADR-045 (amended 2026-08-07) — published state validation for
        ``@app.stream`` and ``@app.device``.
    """
    model_label = _annotation_label(state_model)
    handler_ctx = f" in handler {handler!r}" if handler else ""
    try:
        adapter = _get_adapter(state_model)
        validated = adapter.validate_python(payload)
    except ValidationError as exc:
        raise ReturnValidationError(
            f"Published state does not match state_model {model_label!r}"
            f"{handler_ctx}: {_safe_error_summary(exc)}",
            handler=handler,
            cause=exc,
        ) from exc
    except Exception as exc:
        # Sanitize: never include the payload in the error message.
        raise ReturnValidationError(
            f"Published state could not be validated against state_model "
            f"{model_label!r}{handler_ctx}: {type(exc).__name__}",
            handler=handler,
            cause=exc,
        ) from exc

    try:
        normalised = adapter.dump_python(validated, mode="json")
    except Exception as exc:
        raise ReturnValidationError(
            f"Published state serialisation failed for state_model "
            f"{model_label!r}{handler_ctx}: {type(exc).__name__}",
            handler=handler,
            cause=exc,
        ) from exc

    if isinstance(normalised, dict):
        return normalised  # type: ignore[return-value]
    return {"value": normalised}


# ---------------------------------------------------------------------------
# Convenience helpers for runner modules
# ---------------------------------------------------------------------------


@functools.cache
def get_return_annotation(func: Any) -> Any:
    """Return the resolved return annotation for *func*, with caching.

    Caches the result per function identity so that hot-path runners
    (command dispatch, telemetry cycles) do not call ``get_type_hints``
    on every invocation.  ``lru_cache`` provides thread-safe caching
    under both CPython GIL and free-threaded Python (PEP 703).

    Args:
        func: The handler function.

    Returns:
        The resolved return annotation, or ``None`` if unavailable.
    """
    try:
        return typing.get_type_hints(func).get("return")
    except Exception:
        return None


def normalize_handler_return(
    func: Any,
    value: Any,
    state_model: type | None,
    *,
    handler_name: str | None = None,
) -> dict[str, Any] | None:
    """Normalise a handler return value using *state_model* or its annotation.

    Centralised helper used by both command and telemetry runners so the
    same logic is not duplicated across modules.  Calls
    :func:`get_return_annotation` (cached) to avoid repeated
    ``get_type_hints`` calls on every dispatch or telemetry cycle.

    Args:
        func: The handler function (used for annotation lookup).
        value: The raw return value from the handler.
        state_model: Explicitly declared state contract.  When present it
            is authoritative and outranks the return annotation, which is
            consulted only when *state_model* is ``None`` (ADR-068 clause A).
        handler_name: Handler name for error messages.

    Returns:
        A JSON-compatible dict, or ``None`` to suppress publish.

    Raises:
        ReturnValidationError: If serialisation fails.
    """
    annotation = state_model or get_return_annotation(func)
    return normalize_return(value, annotation, handler=handler_name)
