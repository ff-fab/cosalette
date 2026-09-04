"""Shared registration helpers for App and Router to eliminate duplication."""

from __future__ import annotations

import inspect
import os
import sys
import warnings
from collections.abc import Callable
from types import FrameType, NoneType, UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from cosalette._injection import build_injection_plan
from cosalette._registration._model import _ReactorRegistration
from cosalette._runners._contracts import _annotation_label, get_return_annotation
from cosalette._runners._stream_types import Stream
from cosalette._utils import _callable_qualname

# Directory of the ``cosalette`` package — frames below it are framework
# frames, so warnings raised from here must skip past them (ADR-068 clause F).
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_adapter_tuple(
    port_type: type,
    value: tuple[Any, ...],
) -> tuple[
    type | str | Callable[..., object],
    type | str | Callable[..., object],
]:
    """Parse an adapter (impl, dry_run) 2-tuple, raising on invalid length.

    Args:
        port_type: The port Protocol type being registered.
        value: The tuple to validate and unpack.

    Returns:
        Tuple of (impl, dry_run_impl).

    Raises:
        ValueError: If tuple length != 2.
    """
    if len(value) != 2:  # noqa: PLR2004
        msg = (
            f"adapters value for {port_type!r} must be an impl "
            f"or (impl, dry_run) 2-tuple, got {len(value)}-tuple"
        )
        raise ValueError(msg)
    impl = cast(
        type | str | Callable[..., object],
        value[0],
    )
    dry_run_impl = cast(
        type | str | Callable[..., object],
        value[1],
    )
    return impl, dry_run_impl


def process_adapters_dict(
    adapters: dict[
        type,
        type
        | str
        | Callable[..., object]
        | tuple[
            type | str | Callable[..., object],
            type | str | Callable[..., object],
        ],
    ]
    | None,
    register_func: Callable[
        [
            type,
            type | str | Callable[..., object],
            type | str | Callable[..., object] | None,
        ],
        None,
    ],
) -> None:
    """Process an adapters dict, parsing tuples and calling register_func.

    Args:
        adapters: The adapters mapping from port types to impls or
            (impl, dry_run) tuples.
        register_func: Callback to register each adapter, signature
            (port_type, impl, dry_run).
    """
    if adapters is None:
        return
    for port_type, value in adapters.items():
        if isinstance(value, tuple):
            impl, dry_run_impl = parse_adapter_tuple(port_type, value)
            register_func(port_type, impl, dry_run_impl)
        else:
            register_func(port_type, value, None)


def _collect_stream_params(
    func: Callable[..., Any], hints: dict[str, Any]
) -> list[tuple[str, type]]:
    """Return [(param_name, item_type)] for all Stream[T] params in hints.

    Args:
        func: The function being validated.
        hints: Type hints dict from get_type_hints().

    Returns:
        List of (param_name, item_type) tuples for all Stream[T] parameters.

    Raises:
        TypeError: If Stream is used without type parameter.
    """
    stream_params = []
    for param_name, annotation in hints.items():
        if annotation is Stream:
            msg = (
                f"Stream parameter '{param_name}' in {_callable_qualname(func)} "
                "must be parameterized: Stream[T]"
            )
            raise TypeError(msg)
        if get_origin(annotation) is Stream:
            args = get_args(annotation)
            stream_params.append((param_name, args[0]))
    return stream_params


def validate_stream_signature(
    func: Callable[..., Any],
) -> tuple[list[tuple[str, type]], dict[str, Any]]:
    """Validate that func declares at least one Stream[T] parameter.

    Args:
        func: The stream handler function.

    Returns:
        Tuple of (stream_params, type_hints) where stream_params is a list
        of (param_name, item_type) tuples.

    Raises:
        TypeError: If type hints cannot be resolved or no Stream[T] parameter found.
    """
    try:
        hints = get_type_hints(func)
    except (NameError, AttributeError) as e:
        msg = f"Cannot resolve type hints for {_callable_qualname(func)}: {e}"
        raise TypeError(msg) from e

    stream_params = _collect_stream_params(func, hints)
    if not stream_params:
        msg = f"Function {_callable_qualname(func)} must declare a Stream[T] parameter"
        raise TypeError(msg)
    return stream_params, hints


def build_reactor_registration(
    func: Callable[..., Any],
    state_type: type,
    drain: Callable[[Any], Any] | None,
    reactor_list: list[_ReactorRegistration],
) -> Callable[..., Any]:
    """Build and append a _ReactorRegistration, return func unchanged.

    Args:
        func: The reactor function to register.
        state_type: The state type this reactor subscribes to.
        drain: Optional drain callable to invoke on the state instance.
        reactor_list: The list to append the registration to.

    Returns:
        The original func unchanged.
    """
    # Detect if function declares 'events' parameter
    sig = inspect.signature(func)
    events_param = "events" if "events" in sig.parameters else None

    # Build injection plan, skipping 'events' if present
    reserved_params = {"events"} if events_param else set()
    injection_plan = build_injection_plan(func, mqtt_params=reserved_params)

    registration = _ReactorRegistration(
        state_type=state_type,
        func=func,
        injection_plan=injection_plan,
        drain=drain,
        events_param=events_param,
    )

    reactor_list.append(registration)
    return func


def _user_stacklevel() -> int:
    """Return the ``warnings.warn`` stacklevel of the first non-framework frame.

    Registration reaches this module through a variable number of internal
    frames depending on the entry point (``@app.telemetry`` vs
    ``app.add_telemetry`` vs ``@router.command`` …), so a fixed stacklevel
    would point at framework code for most of them.
    """
    level = 1
    frame: FrameType | None = sys._getframe(1)  # noqa: SLF001
    while frame is not None and frame.f_code.co_filename.startswith(_PACKAGE_ROOT):
        level += 1
        frame = frame.f_back
    return level


def state_model_conflict_labels(
    func: Callable[..., Any],
    state_model: Any,
) -> tuple[str, str] | None:
    """Return ``(declared_model, effective_annotation)`` labels on drift, else ``None``.

    ADR-068 clause A makes ``state_model=`` authoritative, so a differently
    typed return annotation is a silent contradiction.  Same-type — and
    ``-> M | None``, where ``None`` merely suppresses the publish — is not a
    contradiction, nor is a handler with no annotation.  Neither is ``-> None``:
    it promises no return value at all, so clause A never gets to override
    anything — a ``None`` return suppresses the publish before any adapter is
    consulted, and ``state_model=`` there is pure channel metadata (an
    ``@app.command`` state channel in AsyncAPI).

    The labels are the single source of both renderings of this fact: the
    clause F registration warning and the ADR-069 ``_meta/state_model_drift``
    snapshot.
    """
    if state_model is None:
        return None
    annotation = get_return_annotation(func)
    if (
        annotation is None
        or annotation is NoneType
        or _strip_optional(annotation) == _strip_optional(state_model)
    ):
        return None
    return _annotation_label(state_model), _annotation_label(annotation)


def warn_on_state_model_conflict(
    func: Callable[..., Any],
    state_model: Any,
    name: str,
) -> None:
    """Warn when ``state_model=`` and the return annotation names different types.

    Clause F of ADR-068 makes the contradiction visible at registration without
    failing the registration.  See :func:`state_model_conflict_labels` for which
    declarations count as drift.

    Args:
        func: The handler being registered.
        state_model: The declared ``state_model=``, if any.
        name: Resolved registration name, for the warning text.
    """
    labels = state_model_conflict_labels(func, state_model)
    if labels is None:
        return
    declared, effective = labels
    warnings.warn(
        f"Handler {name!r} declares state_model={declared} but is annotated "
        f"-> {effective}. state_model= wins; the return annotation is ignored "
        f"for validation (ADR-068).",
        UserWarning,
        stacklevel=_user_stacklevel(),
    )


def _strip_optional(annotation: Any) -> Any:
    """Return ``T`` for ``T | None``, otherwise *annotation* unchanged."""
    if get_origin(annotation) in (UnionType, Union):
        args = [a for a in get_args(annotation) if a is not NoneType]
        if len(args) == 1:
            return args[0]
    return annotation
