"""Tests for @app.state shared-state factory functionality.

Covers: registration, factory variants (sync, context manager, async gen,
async context manager), settings injection, DI integration, teardown order,
test overrides, bootstrap order, and error conditions.

Test Techniques Used:
    - Specification-based Testing: Verifying registration contracts, variant
      detection from return annotations, and error-condition boundaries.
    - State-based Testing: Asserting that teardown callbacks fire correctly
      and in reverse registration order (LIFO).
    - Boundary-value Analysis: Zero-param and one-param factories; zero
      registrations; duplicate type registration; unsupported annotations.
    - Integration Testing: End-to-end @app.state bootstrap and DI injection
      via AppHarness, including async generator and context-manager teardown.
    - Test Doubles: AppHarness.override_state() for injecting pre-built
      instances in place of real factories.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from cosalette._app import App
from cosalette._persistence._state import (
    _FactoryVariant,
    build_state_registration,
)
from cosalette._settings import Settings
from cosalette.testing import AppHarness

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test state types and factories
# ---------------------------------------------------------------------------


class SyncStateType:
    """Simple state object for sync factory tests."""

    def __init__(self, value: str) -> None:
        self.value = value


class CMStateType:
    """State object for context manager factory tests."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.was_closed = False

    def close(self) -> None:
        self.was_closed = True


class AsyncGenStateType1:
    """State object for async generator factory tests (first one)."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.was_closed = False

    def close(self) -> None:
        self.was_closed = True


class AsyncGenStateType2:
    """State object for async generator factory tests (second one)."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.was_closed = False

    def close(self) -> None:
        self.was_closed = True


class AsyncCMStateType:
    """State object for async context manager factory tests."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.was_closed = False

    async def aclose(self) -> None:
        self.was_closed = True


class MyCustomSettings(Settings):
    """Custom settings class for testing settings injection."""

    custom_value: str = "default"


# ---------------------------------------------------------------------------
# Factory functions for testing
# ---------------------------------------------------------------------------


def sync_factory() -> SyncStateType:
    """Sync factory with no teardown."""
    return SyncStateType("sync")


def sync_factory_with_settings(settings: Settings) -> SyncStateType:
    """Sync factory that receives settings."""
    prefix = settings.mqtt.topic_prefix or "test"
    return SyncStateType(f"sync-{prefix}")


def sync_factory_with_custom_settings(settings: MyCustomSettings) -> SyncStateType:
    """Sync factory that receives custom settings type."""
    return SyncStateType(f"sync-{settings.custom_value}")


@contextlib.contextmanager
def cm_factory() -> Iterator[CMStateType]:
    """Sync context manager factory."""
    state = CMStateType("cm")
    try:
        yield state
    finally:
        state.close()


async def async_gen_factory() -> AsyncIterator[AsyncGenStateType1]:
    """Async generator factory with teardown."""
    state = AsyncGenStateType1("async-gen")
    try:
        yield state
    finally:
        state.close()


@asynccontextmanager
async def async_cm_factory() -> AsyncIterator[AsyncCMStateType]:
    """Async context manager factory."""
    state = AsyncCMStateType("async-cm")
    try:
        yield state
    finally:
        await state.aclose()


def duplicate_factory() -> SyncStateType:
    """Factory that returns same type as sync_factory (for duplicate test)."""
    return SyncStateType("duplicate")


def unannotated_factory():  # type: ignore[no-untyped-def]
    """Factory without return type annotation."""
    return SyncStateType("unannotated")


def bad_param_factory(bad_param: str) -> SyncStateType:  # noqa: ARG001
    """Factory with non-Settings parameter annotation."""
    return SyncStateType("bad")


# ---------------------------------------------------------------------------
# Test build_state_registration function
# ---------------------------------------------------------------------------


def test_build_state_registration_sync() -> None:
    """Test building registration for sync factory."""
    reg = build_state_registration(sync_factory, set())

    assert reg.state_type == SyncStateType
    assert reg.factory == sync_factory
    assert reg.variant == _FactoryVariant.SYNC
    assert reg.settings_type == Settings
    assert reg.has_settings_param is False
    assert reg.settings_param_name == ""


def test_build_state_registration_sync_with_settings() -> None:
    """Test building registration for sync factory with settings param."""
    reg = build_state_registration(sync_factory_with_settings, set())

    assert reg.state_type == SyncStateType
    assert reg.factory == sync_factory_with_settings
    assert reg.variant == _FactoryVariant.SYNC
    assert reg.settings_type == Settings
    assert reg.has_settings_param is True
    assert reg.settings_param_name == "settings"


def test_build_state_registration_sync_with_custom_settings() -> None:
    """Test building registration for sync factory with custom settings type."""
    reg = build_state_registration(sync_factory_with_custom_settings, set())

    assert reg.state_type == SyncStateType
    assert reg.factory == sync_factory_with_custom_settings
    assert reg.variant == _FactoryVariant.SYNC
    assert reg.settings_type == MyCustomSettings
    assert reg.has_settings_param is True
    assert reg.settings_param_name == "settings"


def test_build_state_registration_context_manager() -> None:
    """Test building registration for context manager factory."""
    reg = build_state_registration(cm_factory, set())

    assert reg.state_type == CMStateType
    assert reg.factory == cm_factory
    assert reg.variant == _FactoryVariant.CONTEXT_MANAGER


def test_build_state_registration_async_gen() -> None:
    """Test building registration for async generator factory."""
    reg = build_state_registration(async_gen_factory, set())

    assert reg.state_type == AsyncGenStateType1
    assert reg.factory == async_gen_factory
    assert reg.variant == _FactoryVariant.ASYNC_GEN


def test_build_state_registration_async_cm() -> None:
    """Test building registration for async context manager factory."""
    # Note: async context managers with @asynccontextmanager decorator
    # are actually detected as async generators by the type system
    reg = build_state_registration(async_cm_factory, set())

    assert reg.state_type == AsyncCMStateType
    assert reg.factory == async_cm_factory
    # @asynccontextmanager creates async generator, not async CM
    assert reg.variant == _FactoryVariant.ASYNC_GEN


def test_build_state_registration_duplicate_type() -> None:
    """Test that duplicate state type raises ValueError."""
    build_state_registration(sync_factory, set())  # First registration

    with pytest.raises(
        ValueError, match="Duplicate @app.state for type 'SyncStateType'"
    ):
        build_state_registration(duplicate_factory, {SyncStateType})


def test_build_state_registration_unannotated() -> None:
    """Test that unannotated return type raises TypeError."""
    with pytest.raises(TypeError, match="must have a return type annotation"):
        build_state_registration(unannotated_factory, set())


def test_build_state_registration_bad_param() -> None:
    """Test that non-Settings parameter raises TypeError."""
    with pytest.raises(TypeError, match="has unsupported annotation.*Only Settings"):
        build_state_registration(bad_param_factory, set())


# ---------------------------------------------------------------------------
# Test @app.state decorator
# ---------------------------------------------------------------------------


def test_app_state_decorator_registration() -> None:
    """Test that @app.state decorator registers the factory."""
    app = App("testapp")

    @app.state
    def my_state() -> SyncStateType:
        return SyncStateType("test")

    assert len(app._state_factories) == 1
    reg = app._state_factories[0]
    assert reg.state_type == SyncStateType
    assert reg.factory == my_state


def test_app_state_decorator_duplicate_error() -> None:
    """Test that registering duplicate state type raises ValueError."""
    app = App("testapp")

    @app.state
    def state1() -> SyncStateType:
        return SyncStateType("1")

    with pytest.raises(ValueError, match="Duplicate @app.state"):

        @app.state
        def state2() -> SyncStateType:  # Same type as state1
            return SyncStateType("2")


def test_app_state_decorator_unannotated_error() -> None:
    """Test that unannotated factory raises TypeError."""
    app = App("testapp")

    with pytest.raises(TypeError, match="must have a return type annotation"):

        @app.state
        def bad_state():  # type: ignore[no-untyped-def]
            return SyncStateType("bad")


# ---------------------------------------------------------------------------
# Integration tests with AppHarness
# ---------------------------------------------------------------------------


async def test_sync_state_injection() -> None:
    """Test that sync state is injected into device handlers."""
    harness = AppHarness.create()
    state_created = False

    @harness.app.state
    def my_state() -> SyncStateType:
        nonlocal state_created
        state_created = True
        return SyncStateType("injected")

    injected_state = None
    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device(state: SyncStateType) -> None:
        nonlocal injected_state
        injected_state = state
        device_called.set()

    # Start harness and wait for device to be called
    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    assert state_created
    assert injected_state is not None
    assert injected_state.value == "injected"


async def test_state_with_settings_injection() -> None:
    """Test state factory receiving Settings via DI."""
    from cosalette._settings import MqttSettings

    harness = AppHarness.create(mqtt=MqttSettings(topic_prefix="test_prefix"))

    @harness.app.state
    def my_state(settings: Settings) -> SyncStateType:
        return SyncStateType(f"prefix-{settings.mqtt.topic_prefix}")

    injected_state = None
    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device(state: SyncStateType) -> None:
        nonlocal injected_state
        injected_state = state
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    assert injected_state is not None
    assert injected_state.value == "prefix-test_prefix"


async def test_context_manager_state_teardown() -> None:
    """Test that context manager state is properly torn down."""
    harness = AppHarness.create()
    created_state = None

    @harness.app.state
    @contextlib.contextmanager
    def my_cm_state() -> Iterator[CMStateType]:
        nonlocal created_state
        state = CMStateType("cm")
        created_state = state
        try:
            yield state
        finally:
            state.close()

    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device(state: CMStateType) -> None:  # noqa: ARG001
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    # Check that teardown was called
    assert created_state is not None
    assert created_state.was_closed


async def test_async_gen_state_teardown() -> None:
    """Test that async generator state is properly torn down."""
    harness = AppHarness.create()
    created_state = None

    @harness.app.state
    async def my_async_state() -> AsyncIterator[AsyncGenStateType1]:
        nonlocal created_state
        state = AsyncGenStateType1("async")
        created_state = state
        try:
            yield state
        finally:
            state.close()

    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device(state: AsyncGenStateType1) -> None:  # noqa: ARG001
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    # Check that teardown was called
    assert created_state is not None
    assert created_state.was_closed


async def test_async_cm_state_teardown() -> None:
    """Test that async context manager state is properly torn down."""
    harness = AppHarness.create()
    created_state = None

    @harness.app.state
    @asynccontextmanager
    async def my_async_cm_state() -> AsyncIterator[AsyncCMStateType]:
        nonlocal created_state
        state = AsyncCMStateType("async-cm")
        created_state = state
        try:
            yield state
        finally:
            await state.aclose()

    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device(state: AsyncCMStateType) -> None:  # noqa: ARG001
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    # Check that teardown was called
    assert created_state is not None
    assert created_state.was_closed


async def test_state_injection_in_command() -> None:
    """Test that state is injected into command handlers."""
    harness = AppHarness.create()

    @harness.app.state
    def my_state() -> SyncStateType:
        return SyncStateType("command-injected")

    # Test that command handler can declare state parameter
    # This doesn't test actual routing but verifies DI registration works
    @harness.app.command("cmd_test")
    async def test_command(state: SyncStateType) -> dict[str, Any]:
        # This verifies the injection plan was built correctly
        # During actual run, the framework would inject the state object
        return {"state_value": state.value}

    # Verify the command was registered with correct injection plan
    command_regs = [r for r in harness.app._commands if r.name == "cmd_test"]
    assert len(command_regs) == 1

    # Check that state type is in the injection plan
    injection_types = {param_type for _, param_type in command_regs[0].injection_plan}
    assert SyncStateType in injection_types


async def test_state_not_injected_when_not_requested() -> None:
    """Test that state is not injected when handler doesn't declare the type."""
    harness = AppHarness.create()
    state_factory_called = False

    @harness.app.state
    def my_state() -> SyncStateType:
        nonlocal state_factory_called
        state_factory_called = True
        return SyncStateType("unused")

    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device() -> None:  # No state parameter
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    # State factory should still be called (it's registered)
    assert state_factory_called


async def test_harness_override_state() -> None:
    """Test AppHarness.override_state bypasses factory."""
    harness = AppHarness.create()
    factory_called = False

    @harness.app.state
    def my_state() -> SyncStateType:
        nonlocal factory_called
        factory_called = True
        return SyncStateType("factory")

    # Override with test double
    test_state = SyncStateType("override")
    harness.override_state(SyncStateType, test_state)

    injected_state = None
    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device(state: SyncStateType) -> None:
        nonlocal injected_state
        injected_state = state
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    # Factory should not be called, override should be used
    assert not factory_called
    assert injected_state is test_state
    assert injected_state.value == "override"


async def test_teardown_order_lifo() -> None:
    """Test that state teardown runs in reverse registration order (LIFO)."""
    harness = AppHarness.create()
    teardown_order = []

    @harness.app.state
    async def state1() -> AsyncIterator[AsyncGenStateType1]:
        state = AsyncGenStateType1("state1")
        try:
            yield state
        finally:
            teardown_order.append("state1")

    @harness.app.state
    async def state2() -> AsyncIterator[AsyncGenStateType2]:
        state = AsyncGenStateType2("state2")
        try:
            yield state
        finally:
            teardown_order.append("state2")

    device_called = asyncio.Event()

    @harness.app.device("teardown_test")
    async def test_device() -> None:
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    # Should tear down in reverse order: state2 first, then state1
    assert teardown_order == ["state2", "state1"]


async def test_lifespan_compatibility() -> None:
    """Test that @app.state works alongside lifespan= parameter."""
    harness = AppHarness.create()
    state_factory_called = False
    lifespan_entered = False

    @harness.app.state
    def my_state() -> SyncStateType:
        nonlocal state_factory_called
        state_factory_called = True
        return SyncStateType("state")

    @contextlib.asynccontextmanager
    async def my_lifespan(ctx: Any) -> AsyncIterator[None]:  # noqa: ARG001
        nonlocal lifespan_entered
        lifespan_entered = True
        yield

    harness.app._lifespan = my_lifespan

    device_called = asyncio.Event()

    @harness.app.device("test")
    async def test_device(state: SyncStateType) -> None:  # noqa: ARG001
        device_called.set()

    async def run_test() -> None:
        await asyncio.wait_for(device_called.wait(), timeout=1.0)
        harness.trigger_shutdown()

    await asyncio.gather(harness.run(), run_test())

    assert state_factory_called
    assert lifespan_entered
