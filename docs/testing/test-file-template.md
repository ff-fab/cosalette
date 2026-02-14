# Test File Template for Python Tests

> **Scope:** Ready-to-copy template for new pytest test files with all project
> conventions applied. Ensures consistency across the test suite.

## Quick Reference

| Convention           | Pattern                                             |
| -------------------- | --------------------------------------------------- |
| Module docstring     | Document techniques used across the file            |
| Class docstring      | Document technique(s) for that test group           |
| Naming               | `test_<unit>_<behavior>_<condition>`                |
| Side-effect fixtures | `@pytest.mark.usefixtures("fixture_name")` on class |
| Async sync           | `asyncio.Event()` for startup coordination          |
| Async polling        | `wait_for_condition()` for state verification       |
| Assertions           | Specific values, not truthy checks                  |

---

## Complete Template

Note that big headlines (embraced in `===`) are only to divide the test file in major
sections. They are not to be used to highlight individual test classes.

```python
"""Unit tests for <module_path> — <brief description>.

Test Techniques Used:
- <Technique 1>: <what it validates>
- <Technique 2>: <what it validates>

Common techniques:
- Specification-based Testing: Verifying constructor/method contracts
- Equivalence Partitioning: Input domains via @parametrize
- Boundary Value Analysis: Numeric limits, timeouts, thresholds
- State Transition Testing: Lifecycle states, FSMs
- Branch Coverage: Boolean expressions, if/else paths
- Error Guessing: Anticipating specific failure modes
- Round-trip Testing: Serialization/deserialization fidelity
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

# Import module under test
from {{ module_name }}.path.to.module import ClassUnderTest

# Import shared fixtures (test data, async utilities)
from tests.fixtures.async_utils import wait_for_condition
from tests.fixtures.signals import create_signal, TEMPERATURE_SIGNALS


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def instance() -> ClassUnderTest:
    """Fresh instance for each test."""
    return ClassUnderTest()


@pytest.fixture
def mock_external_dependency():
    """Mock external I/O to isolate unit under test.

    Use this pattern for:
    - Network calls
    - File I/O
    - External services (OpenHAB, etc.)
    """
    with patch("{{ module_name }}.path.to.module.external_call") as mock:
        mock.return_value = "mocked_value"
        yield mock


# =============================================================================
# Tests
# =============================================================================


class TestBasicBehavior:
    """Tests for <specific behavior being tested>.

    Technique: Specification-based Testing — verifying public API contracts.
    """

    def test_method_returns_expected_value(self, instance: ClassUnderTest) -> None:
        """Method returns expected value for valid input."""
        # Arrange
        input_value = "test"

        # Act
        result = instance.method(input_value)

        # Assert
        assert result == "expected"

    def test_method_handles_empty_input(self, instance: ClassUnderTest) -> None:
        """Method handles empty input gracefully."""
        result = instance.method("")
        assert result == "default"


class TestErrorHandling:
    """Tests for error conditions and edge cases.

    Technique: Error Guessing — anticipating specific failure modes.
    """

    def test_raises_value_error_for_invalid_input(
        self, instance: ClassUnderTest
    ) -> None:
        """Invalid input raises ValueError with descriptive message."""
        with pytest.raises(ValueError, match="must be positive"):
            instance.method(-1)


@pytest.mark.usefixtures("mock_external_dependency")
class TestWithSideEffectFixture:
    """Tests requiring mocked external dependencies.

    Technique: Isolation Testing — mocking external I/O.

    Note: Using @pytest.mark.usefixtures() because the fixture provides
    side effects (patching) rather than values we need to reference.
    """

    def test_calls_external_service(self, instance: ClassUnderTest) -> None:
        """Method correctly calls external service."""
        result = instance.do_external_call()
        assert result == "mocked_value"


class TestAsyncBehavior:
    """Tests for async operations.

    Technique: State Transition Testing — verifying concurrent state changes.
    """

    async def test_async_operation_completes(
        self, instance: ClassUnderTest
    ) -> None:
        """Async operation completes successfully."""
        result = await instance.async_method()
        assert result is not None

    async def test_concurrent_operations_coordinate(
        self, instance: ClassUnderTest
    ) -> None:
        """Multiple concurrent operations coordinate correctly.

        Uses asyncio.Event for deterministic startup synchronization.
        """
        started = asyncio.Event()
        results: list[str] = []

        async def worker():
            started.set()  # Signal that worker has started
            result = await instance.async_method()
            results.append(result)

        # Start worker and wait for it to be ready
        task = asyncio.create_task(worker())
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Now we know worker is running—perform test actions
        instance.trigger_something()

        # Wait for completion
        await asyncio.wait_for(task, timeout=1.0)
        assert len(results) == 1

    async def test_cleanup_completes_after_cancellation(
        self, instance: ClassUnderTest
    ) -> None:
        """Resources are cleaned up after task cancellation.

        Uses wait_for_condition for polling async state changes.
        """
        task = asyncio.create_task(instance.long_running_operation())

        # Cancel and verify cleanup
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Wait for cleanup to complete (don't use fixed sleep!)
        await wait_for_condition(
            lambda: instance.is_cleaned_up,
            description="cleanup completion",
        )


class TestParametrized:
    """Tests using parametrization for equivalence partitioning.

    Technique: Equivalence Partitioning — testing representative values
    from each input class.
    """

    @pytest.mark.parametrize(
        ("input_value", "expected"),
        [
            ("valid", "processed"),      # Normal case
            ("", "empty"),               # Empty input
            ("SPECIAL", "special"),      # Special handling
        ],
        ids=["normal", "empty", "special"],
    )
    def test_processes_input_correctly(
        self,
        instance: ClassUnderTest,
        input_value: str,
        expected: str,
    ) -> None:
        """Each input class produces correct output."""
        result = instance.process(input_value)
        assert result == expected


class TestBoundaryValues:
    """Tests for boundary conditions.

    Technique: Boundary Value Analysis — testing at and around limits.
    """

    @pytest.mark.parametrize(
        ("value", "expected_valid"),
        [
            (0, True),      # Minimum valid
            (1, True),      # Just above minimum
            (99, True),     # Just below maximum
            (100, True),    # Maximum valid
            (-1, False),    # Below minimum (invalid)
            (101, False),   # Above maximum (invalid)
        ],
        ids=["min", "min+1", "max-1", "max", "below_min", "above_max"],
    )
    def test_validates_range_boundaries(
        self,
        instance: ClassUnderTest,
        value: int,
        expected_valid: bool,
    ) -> None:
        """Range validation correctly handles boundary values."""
        result = instance.is_valid(value)
        assert result == expected_valid
```

---

## Key Patterns Explained

### 1. Module Docstring with Techniques

Every test file starts with a docstring documenting which test design techniques are
used. This provides traceability to ISTQB principles.

```python
"""Unit tests for state/store.py — Signal storage and pub/sub.

Test Techniques Used:
- State Transition Testing: Store population lifecycle
- Error Guessing: Concurrent modification edge cases
- Boundary Value Analysis: Queue capacity limits
"""
```

### 2. Class Docstrings with Technique

Each test class documents its specific technique:

```python
class TestSubscribe:
    """Tests for the subscribe() async generator.

    Technique: State Transition Testing — subscriber lifecycle from
    connection through cancellation and cleanup.
    """
```

### 3. `@pytest.mark.usefixtures()` for Side-Effect Fixtures

When a fixture provides side effects (like patching) rather than values you need to
reference, use the decorator instead of a parameter:

```python
# ✅ Good: Fixture provides patching, we don't need the mock object
@pytest.mark.usefixtures("mock_settings")
class TestWithMockedSettings:
    def test_uses_default_settings(self) -> None:
        store = SignalStore()  # Uses mocked settings automatically
        assert store.queue_size == 100


# ❌ Avoid: Parameter not used, triggers linter warnings
class TestWithMockedSettings:
    def test_uses_default_settings(self, mock_settings: object) -> None:
        _ = mock_settings  # Suppress warning
        store = SignalStore()
```

### 4. `asyncio.Event()` for Startup Synchronization

When coordinating concurrent coroutines, use `Event` for deterministic synchronization:

```python
async def test_subscriber_receives_updates(self) -> None:
    store = SignalStore()
    received: list[Signal] = []
    started = asyncio.Event()  # Coordination mechanism

    async def subscriber():
        started.set()  # Signal ready
        async for signal in store.subscribe():
            received.append(signal)

    task = asyncio.create_task(subscriber())
    await asyncio.wait_for(started.wait(), timeout=1.0)  # Wait for ready

    # Now we know subscriber is listening
    await store.set(signal)
```

### 5. `wait_for_condition()` for State Verification

When waiting for async state changes (not coordinating startup), use polling:

```python
from tests.fixtures.async_utils import wait_for_condition

async def test_subscriber_cleanup_after_cancel(self) -> None:
    store = SignalStore()
    task = asyncio.create_task(consume(store.subscribe()))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Wait for cleanup (don't use asyncio.sleep!)
    await wait_for_condition(
        lambda: store.subscriber_count() == 0,
        description="subscriber cleanup",
    )
```

---

## Shared Fixtures Reference

### `tests/fixtures/async_utils.py`

```python
from tests.fixtures.async_utils import wait_for_condition

# Poll until condition is true, or timeout
await wait_for_condition(
    lambda: store.subscriber_count() == 0,
    timeout=1.0,           # Maximum wait (default: 1.0s)
    interval=0.005,        # Poll interval (default: 5ms)
    description="cleanup", # For timeout error message
)
```

### `tests/fixtures/signals.py`

```python
from tests.fixtures.signals import (
    create_signal,           # Factory with defaults
    TEMPERATURE_SIGNALS,     # Pre-built signal list
    SWITCH_SIGNALS,
    POWER_SIGNALS,
    SPECIAL_STATE_SIGNALS,
    ALL_TEST_SIGNALS,
)

# Create custom signal
signal = create_signal(id="test:temp", value="21.5", unit="°C")

# Use pre-built collections
for signal in TEMPERATURE_SIGNALS:
    await store.set(signal)
```

---

## Anti-Patterns to Avoid

### ❌ Fixed Sleeps for Synchronization

```python
# Bad: Flaky, slow
await asyncio.sleep(0.1)
assert store.subscriber_count() == 0
```

### ❌ Accessing Private Variables

```python
# Bad: Couples tests to implementation
assert store._internal_dict["key"] == value
```

### ❌ Vague Assertions

```python
# Bad: Doesn't explain what's expected
assert result
assert len(items)
```

### ❌ Over-Mocking

```python
# Bad: Tests nothing real
mock_store.get.return_value = signal
result = mock_store.get("id")
assert result == signal  # Proves nothing
```
