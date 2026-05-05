"""Unit tests for @app.react domain-event reactor registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

import cosalette


@dataclass
class MockState:
    """Mock state object for testing reactors."""

    events: list[str] = field(default_factory=list)
    _pending_events: list[str] = field(default_factory=list)

    def drain_events(self) -> list[str]:
        """Drain and clear pending events."""
        events = self._pending_events.copy()
        self._pending_events.clear()
        return events

    def add_event(self, event: str) -> None:
        """Add an event to the pending list."""
        self._pending_events.append(event)


@dataclass
class CustomDrainState:
    """State with a custom drain method."""

    _events: list[str] = field(default_factory=list)

    def custom_drain(self) -> list[str]:
        """Custom drain method."""
        events = self._events.copy()
        self._events.clear()
        return events

    def add_event(self, event: str) -> None:
        """Add an event."""
        self._events.append(event)


@dataclass
class AsyncDrainState:
    """State with an async drain method."""

    _events: list[str] = field(default_factory=list)

    async def async_drain_events(self) -> list[str]:
        """Async drain method."""
        events = self._events.copy()
        self._events.clear()
        return events

    def add_event(self, event: str) -> None:
        """Add an event."""
        self._events.append(event)


@dataclass
class FailingState:
    """State with a failing drain method."""

    def drain_events(self) -> list[str]:
        """Drain method that always fails."""
        raise RuntimeError("Drain failed")


@dataclass
class FailingReactorState:
    """State for testing reactor function failures."""

    _events: list[str] = field(default_factory=list)

    def drain_events(self) -> list[str]:
        """Drain events."""
        events = self._events.copy()
        self._events.clear()
        return events

    def add_event(self, event: str) -> None:
        """Add an event."""
        self._events.append(event)


@dataclass
class NonIterableDrainState:
    """State with drain that returns non-iterable scalar."""

    def drain_events(self) -> int:
        """Drain that returns a non-iterable scalar."""
        return 42


@pytest.mark.unit
class TestReactRegistration:
    """Test @app.react decorator registration."""

    def test_react_registration_succeeds_with_registered_state(self) -> None:
        """@app.react succeeds when state_type is registered via @app.state."""
        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> MockState:
            return MockState()

        # Act & Assert — should not raise
        @app.react(MockState)
        async def handle_events(events: list[str]) -> None:
            pass

        assert len(app._reactors) == 1
        reg = app._reactors[0]
        assert reg.state_type is MockState
        assert reg.func is handle_events
        assert reg.events_param == "events"

    def test_react_fails_with_unregistered_state(self) -> None:
        """@app.react raises ValueError when state_type is not registered."""
        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        # Act & Assert
        with pytest.raises(ValueError, match="State type.*not registered"):

            @app.react(MockState)
            async def handle_events(events: list[str]) -> None:
                pass

    def test_react_fails_with_sync_function(self) -> None:
        """@app.react raises TypeError when function is not async."""
        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> MockState:
            return MockState()

        # Act & Assert
        with pytest.raises(TypeError, match="must be async"):

            @app.react(MockState)
            def handle_events(events: list[str]) -> None:
                pass

    def test_react_with_custom_drain(self) -> None:
        """@app.react accepts custom drain callable."""
        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> CustomDrainState:
            return CustomDrainState()

        # Act
        @app.react(CustomDrainState, drain=lambda state: state.custom_drain())
        async def handle_events(events: list[str]) -> None:
            pass

        # Assert
        assert len(app._reactors) == 1
        reg = app._reactors[0]
        assert reg.drain is not None

    def test_react_detects_events_parameter(self) -> None:
        """@app.react detects 'events' parameter and skips it from DI."""
        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> MockState:
            return MockState()

        # Act
        @app.react(MockState)
        async def handle_events(
            events: list[str],
            ctx: cosalette.DeviceContext,
            logger: logging.Logger,
        ) -> None:
            pass

        # Assert
        reg = app._reactors[0]
        assert reg.events_param == "events"
        # Should have ctx and logger in injection plan, but not events
        param_names = {name for name, _ in reg.injection_plan}
        assert "ctx" in param_names
        assert "logger" in param_names
        assert "events" not in param_names

    def test_react_without_events_parameter(self) -> None:
        """@app.react works when function doesn't declare 'events' parameter."""
        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> MockState:
            return MockState()

        # Act
        @app.react(MockState)
        async def handle_events(ctx: cosalette.DeviceContext) -> None:
            pass

        # Assert
        reg = app._reactors[0]
        assert reg.events_param is None
        param_names = {name for name, _ in reg.injection_plan}
        assert "ctx" in param_names
        assert "events" not in param_names

    def test_multiple_reactors_for_same_state(self) -> None:
        """Multiple @app.react decorators for same state type are allowed."""
        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> MockState:
            return MockState()

        # Act
        @app.react(MockState)
        async def reactor_one(events: list[str]) -> None:
            pass

        @app.react(MockState)
        async def reactor_two(events: list[str]) -> None:
            pass

        # Assert
        assert len(app._reactors) == 2
        assert app._reactors[0].func is reactor_one
        assert app._reactors[1].func is reactor_two


@pytest.mark.unit
class TestReactorDispatcher:
    """Test reactor event dispatching."""

    @pytest.fixture
    def app_with_state(self) -> cosalette.App:
        """App with a registered state factory."""
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> MockState:
            return MockState()

        return app

    async def test_reactor_called_when_events_present(
        self, app_with_state: cosalette.App
    ) -> None:
        """Reactor is called when state has pending events."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        events_received: list[str] = []

        @app_with_state.react(MockState)
        async def handle_events(events: list[str], state: MockState) -> None:
            events_received.extend(events)

        # Create state instance and add events
        state = MockState()
        state.add_event("event1")
        state.add_event("event2")

        # Build providers with state instance
        providers = {MockState: state}

        # Act
        await dispatch_reactors(app_with_state._reactors, providers)

        # Assert
        assert events_received == ["event1", "event2"]
        assert state.drain_events() == []  # Events should be drained

    async def test_reactor_not_called_when_no_events(
        self, app_with_state: cosalette.App
    ) -> None:
        """Reactor is not called when state has no pending events."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        call_count = 0

        @app_with_state.react(MockState)
        async def handle_events(events: list[str]) -> None:
            nonlocal call_count
            call_count += 1

        # Create empty state instance
        state = MockState()
        providers = {MockState: state}

        # Act
        await dispatch_reactors(app_with_state._reactors, providers)

        # Assert
        assert call_count == 0

    async def test_reactor_with_custom_drain(self) -> None:
        """Reactor uses custom drain callable when provided."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> CustomDrainState:
            return CustomDrainState()

        events_received: list[str] = []

        @app.react(CustomDrainState, drain=lambda state: state.custom_drain())
        async def handle_events(events: list[str]) -> None:
            events_received.extend(events)

        # Create state and add events
        state = CustomDrainState()
        state.add_event("custom_event")
        providers = {CustomDrainState: state}

        # Act
        await dispatch_reactors(app._reactors, providers)

        # Assert
        assert events_received == ["custom_event"]

    async def test_reactor_with_dependency_injection(
        self, app_with_state: cosalette.App
    ) -> None:
        """Reactor receives injected dependencies."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        received_deps: dict[str, Any] = {}

        @app_with_state.react(MockState)
        async def handle_events(
            events: list[str], state: MockState, logger: logging.Logger
        ) -> None:
            received_deps["events"] = events
            received_deps["state"] = state
            received_deps["logger"] = logger

        # Create state instance and add events
        state = MockState()
        state.add_event("test_event")

        # Build providers with logger
        providers = {
            MockState: state,
            logging.Logger: logging.getLogger("test"),
        }

        # Act
        await dispatch_reactors(app_with_state._reactors, providers)

        # Assert
        assert received_deps["events"] == ["test_event"]
        assert received_deps["state"] is state
        assert isinstance(received_deps["logger"], logging.Logger)

    async def test_reactor_handles_async_drain(self) -> None:
        """Reactor handles async drain methods."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> AsyncDrainState:
            return AsyncDrainState()

        events_received: list[str] = []

        @app.react(AsyncDrainState, drain=lambda state: state.async_drain_events())
        async def handle_events(events: list[str]) -> None:
            events_received.extend(events)

        # Create state and add events
        state = AsyncDrainState()
        state.add_event("async_event")
        providers = {AsyncDrainState: state}

        # Act
        await dispatch_reactors(app._reactors, providers)

        # Assert
        assert events_received == ["async_event"]

    async def test_reactor_raises_on_missing_state(
        self, app_with_state: cosalette.App
    ) -> None:
        """Reactor raises ValueError when state instance is missing from providers."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        @app_with_state.react(MockState)
        async def handle_events(events: list[str]) -> None:
            pass

        # Empty providers (no state instance)
        providers: dict[type, Any] = {}

        # Act & Assert
        with pytest.raises(ValueError, match="State type.*not found in providers"):
            await dispatch_reactors(app_with_state._reactors, providers)

    async def test_reactor_raises_on_drain_failure(
        self, app_with_state: cosalette.App
    ) -> None:
        """Reactor raises when drain method fails."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> FailingState:
            return FailingState()

        @app.react(FailingState)
        async def handle_events(events: list[str]) -> None:
            pass

        state = FailingState()
        providers = {FailingState: state}

        # Act & Assert
        with pytest.raises(RuntimeError, match="Drain failed"):
            await dispatch_reactors(app._reactors, providers)

    async def test_reactor_raises_on_function_failure(self) -> None:
        """Reactor raises when the reactor function fails."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> FailingReactorState:
            return FailingReactorState()

        @app.react(FailingReactorState)
        async def handle_events(events: list[str]) -> None:
            raise ValueError("Reactor failed")

        state = FailingReactorState()
        state.add_event("test_event")
        providers = {FailingReactorState: state}

        # Act & Assert
        with pytest.raises(ValueError, match="Reactor failed"):
            await dispatch_reactors(app._reactors, providers)

    async def test_drain_raises_on_non_iterable_result(self) -> None:
        """Drain raises TypeError when result is a non-iterable scalar."""
        from cosalette._reactors import dispatch_reactors

        # Arrange
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> NonIterableDrainState:
            return NonIterableDrainState()

        @app.react(NonIterableDrainState)
        async def handle_events(events: list[str]) -> None:
            pass

        state = NonIterableDrainState()
        providers = {NonIterableDrainState: state}

        # Act & Assert
        with pytest.raises(TypeError, match="Drain result must be None or iterable"):
            await dispatch_reactors(app._reactors, providers)
