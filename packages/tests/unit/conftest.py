"""Shared fixtures and helpers for unit tests in packages/tests/unit/.

Non-fixture helpers (protocol classes, dummy implementations, settings
subclasses) are importable by test modules.  Pytest fixtures (``app``,
etc.) are auto-discovered — do NOT import them explicitly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pytest

from cosalette._app import App
from cosalette._settings import Settings

# ---------------------------------------------------------------------------
# Fixtures (auto-discovered by pytest — never import these)
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> App:
    """Minimal App instance for registration tests."""
    return App(name="testapp", version="1.0.0")


# mock_mqtt and fake_clock fixtures provided by cosalette.testing._plugin


# ---------------------------------------------------------------------------
# Protocol / helper classes (import explicitly in test modules)
# ---------------------------------------------------------------------------


@runtime_checkable
class _DummyPort(Protocol):
    """Dummy protocol for adapter registration tests."""

    def do_thing(self) -> str: ...


class _DummyImpl:
    """Concrete adapter for testing."""

    def do_thing(self) -> str:
        return "real"


class _DummyDryRun:
    """Dry-run adapter for testing."""

    def do_thing(self) -> str:
        return "dry"


class _TestMySettings(Settings):
    """Settings subclass for adapter factory injection tests.

    Defined at module level so ``get_type_hints`` can resolve the
    annotation when ``from __future__ import annotations`` is active
    (PEP 563).  Uses an isolated settings source to avoid picking up
    environment variables during tests.
    """

    custom_value: str = "hello"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[Settings],  # noqa: ARG003
        init_settings: Any,
        env_settings: Any,  # noqa: ARG003
        dotenv_settings: Any,  # noqa: ARG003
        file_secret_settings: Any,  # noqa: ARG003
    ) -> tuple[Any, ...]:
        return (init_settings,)


@runtime_checkable
class _InjectionTestPort(Protocol):
    """Port protocol for injection adapter tests."""

    def value(self) -> int: ...


class _InjectionTestImpl:
    """Concrete adapter for injection adapter tests."""

    def value(self) -> int:
        return 42


@runtime_checkable
class _LifecyclePort(Protocol):
    """Port protocol for lifecycle adapter tests."""

    def get_value(self) -> str: ...


class _LifecycleAdapter:
    """Adapter that implements the async context manager protocol.

    Tracks enter/exit calls and ordering for lifecycle assertions.
    """

    def __init__(self, name: str = "default", log: list[str] | None = None) -> None:
        self.name = name
        self.log = log if log is not None else []
        self.entered = False
        self.exited = False

    def get_value(self) -> str:
        return self.name

    async def __aenter__(self) -> _LifecycleAdapter:
        self.entered = True
        self.log.append(f"{self.name}:enter")
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True
        self.log.append(f"{self.name}:exit")


@runtime_checkable
class _PlainPort(Protocol):
    """Port protocol for a non-lifecycle adapter."""

    def compute(self) -> int: ...


class _PlainAdapter:
    """Adapter without lifecycle protocol — no __aenter__/__aexit__."""

    def compute(self) -> int:
        return 42


@runtime_checkable
class _LifecyclePort2(Protocol):
    """Second port protocol for multi-adapter ordering tests."""

    def label(self) -> str: ...


class _LifecycleAdapter2:
    """Second lifecycle adapter for ordering tests."""

    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log if log is not None else []

    def label(self) -> str:
        return "adapter2"

    async def __aenter__(self) -> _LifecycleAdapter2:
        self.log.append("adapter2:enter")
        return self

    async def __aexit__(self, *args: object) -> None:
        self.log.append("adapter2:exit")


class _FakeFilter:
    """Stub stateful object used to test init= injection.

    Not a real filter — just a container with a type the DI system
    can match and a mutating method to prove persistence.
    """

    def __init__(self, factor: float = 1.0) -> None:
        self.factor = factor
        self.call_count = 0

    def update(self, raw: float) -> float:
        self.call_count += 1
        return raw * self.factor
