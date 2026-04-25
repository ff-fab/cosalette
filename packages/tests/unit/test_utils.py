"""Unit tests for cosalette._utils — general-purpose internal helpers.

Test Techniques Used:
    - Equivalence Partitioning: callables with __qualname__, without it
      (functools.partial), and with no name attributes at all.
    - Boundary Value Analysis: deeply nested partials, callable objects.
    - Error Guessing: the exact AttributeError that prompted this fix.
    - Specification-based Testing: return-value contracts for both helpers.
"""

from __future__ import annotations

import functools
from typing import Any

import pytest

from cosalette._utils import _callable_name, _callable_qualname

pytestmark = pytest.mark.unit


# ===========================================================================
# Helpers
# ===========================================================================


def _plain_function() -> None:
    """Module-level function — has both __name__ and __qualname__."""


class _MyClass:
    def method(self) -> None: ...


class _CallableObject:
    """A callable class instance without __name__ or __qualname__."""

    def __call__(self) -> None: ...


# ===========================================================================
# _callable_qualname
# ===========================================================================


class TestCallableQualname:
    """Verify _callable_qualname returns a string that never raises.

    Technique: Equivalence Partitioning — each callable category is its
    own partition.
    """

    def test_regular_function(self) -> None:
        """Plain functions expose __qualname__ directly."""
        assert _callable_qualname(_plain_function) == "_plain_function"

    def test_method(self) -> None:
        """Bound/unbound methods produce the dotted qualname."""
        assert _callable_qualname(_MyClass.method) == "_MyClass.method"

    def test_lambda(self) -> None:
        """Lambdas have a qualname that starts with '<lambda>'."""
        f = lambda: None  # noqa: E731
        assert "<lambda>" in _callable_qualname(f)

    def test_partial_wrapping_regular_function(self) -> None:
        """functools.partial is formatted as 'partial(<inner_name>)'."""
        p = functools.partial(_plain_function)
        assert _callable_qualname(p) == "partial(_plain_function)"

    def test_partial_with_args(self) -> None:
        """functools.partial uses the inner function's full __qualname__."""

        async def my_handler(ctx: Any) -> None: ...

        p = functools.partial(my_handler, ctx=object())
        # __qualname__ includes the full dotted path (enclosing class/function)
        result = _callable_qualname(p)
        assert result.startswith("partial(")
        assert "my_handler" in result

    def test_nested_partial(self) -> None:
        """Python flattens nested partials — double-wrapped looks like single-wrapped.

        CPython's functools.partial automatically merges a partial-of-partial
        into a single partial with the innermost callable as func.  So
        partial(partial(f)) == partial(f) at the .func level, and our helper
        returns 'partial(_plain_function)' in both cases.
        """
        p = functools.partial(functools.partial(_plain_function))
        # Python flattens: p.func is _plain_function, not another partial
        assert p.func is _plain_function
        assert _callable_qualname(p) == "partial(_plain_function)"

    def test_callable_object_falls_back_to_class_qualname(self) -> None:
        """Objects with __call__ but no __qualname__ use the class qualname.

        Qualname is preferred over name so that nested classes are
        unambiguously identified (e.g. 'Outer.Inner' rather than just 'Inner').
        """
        obj = _CallableObject()
        result = _callable_qualname(obj)
        assert result == "_CallableObject"

    def test_partial_of_callable_object(self) -> None:
        """partial(callable_instance) — both operands lack __qualname__.

        Boundary case: partial.func is a callable object (not a function),
        so the recursion must handle the qualname-less case correctly.
        """
        obj = _CallableObject()
        p = functools.partial(obj)
        result = _callable_qualname(p)
        assert result == "partial(_CallableObject)"

    def test_class_itself(self) -> None:
        """Classes have __qualname__ and return it directly."""
        assert _callable_qualname(_MyClass) == "_MyClass"


# ===========================================================================
# _callable_name
# ===========================================================================


class TestCallableName:
    """Verify _callable_name returns a short name that never raises.

    Technique: Equivalence Partitioning — same callable categories as
    _callable_qualname but expecting __name__ semantics.
    """

    def test_regular_function(self) -> None:
        """Plain functions expose __name__ directly."""
        assert _callable_name(_plain_function) == "_plain_function"

    def test_method_returns_short_name(self) -> None:
        """Methods return __name__, not the dotted qualname."""
        assert _callable_name(_MyClass.method) == "method"

    def test_partial_unwraps_to_inner_name(self) -> None:
        """functools.partial returns the inner function's __name__, no prefix."""
        p = functools.partial(_plain_function)
        assert _callable_name(p) == "_plain_function"

    def test_partial_with_args_unwraps(self) -> None:
        """Partial with pre-filled args still returns the clean inner name."""

        async def my_handler(ctx: Any) -> None: ...

        p = functools.partial(my_handler, ctx=object())
        assert _callable_name(p) == "my_handler"

    def test_nested_partial_unwraps_fully(self) -> None:
        """Double-wrapped partial returns the innermost function's __name__."""
        p = functools.partial(functools.partial(_plain_function))
        assert _callable_name(p) == "_plain_function"

    def test_callable_object_falls_back_to_class_name(self) -> None:
        """Objects with __call__ but no __name__ use the class name."""
        obj = _CallableObject()
        result = _callable_name(obj)
        assert result == "_CallableObject"

    def test_partial_of_callable_object(self) -> None:
        """partial(callable_instance) unwraps to callable object's class name.

        Boundary case mirroring TestCallableQualname — both partial and
        its wrapped object lack __name__, so we must recurse and fall back.
        """
        obj = _CallableObject()
        p = functools.partial(obj)
        result = _callable_name(p)
        assert result == "_CallableObject"

    def test_class_itself(self) -> None:
        """Classes have __name__ and return it directly."""
        assert _callable_name(_MyClass) == "_MyClass"


# ===========================================================================
# Integration — partial accepted by add_telemetry / add_command / add_device
# ===========================================================================


class TestPartialRegistration:
    """Registration APIs must not raise AttributeError for functools.partial.

    Technique: Error Guessing — the exact failure mode reported by users:
    'functools.partial lacks __qualname__ which cosalette requires.'
    """

    def test_add_telemetry_accepts_partial_with_explicit_name(self) -> None:
        """add_telemetry(name, partial_func) must not raise AttributeError."""
        import cosalette

        async def _base_handler() -> dict[str, object]:
            return {"value": 42}

        p = functools.partial(_base_handler)
        app = cosalette.App(name="test-partial", version="0.0.1")
        # Should not raise AttributeError: 'functools.partial' object has
        # no attribute '__qualname__'
        app.add_telemetry("sensor", p, interval=10)
        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "sensor"

    def test_add_command_accepts_partial_with_explicit_name(self) -> None:
        """add_command(name, partial_func) must not raise AttributeError."""
        import cosalette

        async def _base_handler() -> dict[str, object] | None:
            return None

        p = functools.partial(_base_handler)
        app = cosalette.App(name="test-partial", version="0.0.1")
        app.add_command("ctrl", p)
        assert len(app._commands) == 1
        assert app._commands[0].name == "ctrl"

    def test_add_device_accepts_partial_with_explicit_name(self) -> None:
        """add_device(name, partial_func) must not raise AttributeError."""
        import cosalette

        async def _base_handler() -> None: ...

        p = functools.partial(_base_handler)
        app = cosalette.App(name="test-partial", version="0.0.1")
        app.add_device("actuator", p)
        assert len(app._devices) == 1
        assert app._devices[0].name == "actuator"

    def test_add_telemetry_implicit_name_from_partial(self) -> None:
        """Decorator form of @app.telemetry() derives name from inner callable.

        When no explicit name is provided, _callable_name() is called on the
        partial — the result should be the inner function's __name__, not a
        crash.
        """
        import cosalette

        async def my_sensor() -> dict[str, object]:
            return {"value": 42}

        p = functools.partial(my_sensor)
        app = cosalette.App(name="test-partial", version="0.0.1")
        app.telemetry(interval=10)(p)
        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "my_sensor"

    def test_injection_error_message_includes_partial_description(self) -> None:
        """TypeError from build_injection_plan names the partial, not crashes."""
        from cosalette._injection import build_injection_plan

        # A function with an unannotated parameter — injection should raise
        # TypeError with a *readable* message, not AttributeError.
        def _handler(unannotated_param):  # noqa: ANN001
            pass

        p = functools.partial(_handler)
        with pytest.raises(TypeError, match="partial"):
            build_injection_plan(p)
