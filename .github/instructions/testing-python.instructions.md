---
description: 'Python testing - pytest patterns'
applyTo: 'packages/tests/**/*.py'
---

# Testing Instructions

> **New Test Files:** Use
> [Test File Template](../../docs/testing/test-file-template.md) — copy-paste
> starting point with all conventions pre-applied.

## Test Strategy (ADR-006)

| Layer       | Tool                           | Location             |
| ----------- | ------------------------------ | -------------------- |
| Unit        | pytest + pytest-asyncio        | `tests/unit/`        |

| Integration | pytest + httpx/ASGI client     | `tests/integration/` |


## Test Technique Documentation

**Every test must document the test design technique(s) used.** This ensures
traceability to ISTQB principles and helps reviewers understand why specific test cases
were chosen.

### Module-Level Docstring

Document techniques used across the test module:

```python
"""Unit tests for adapters/manager.py — Adapter lifecycle management.

Test Techniques Used:
- State Transition Testing: Adapter lifecycle states (stopped → starting → running)
- Boundary Value Analysis: Retry delays at INITIAL (5s) and MAX (300s)
- Condition Coverage: Error handling branches
"""
```

### Class/Function-Level Documentation

For non-obvious technique choices, document at the test level:

```python
def test_frozen_immutability(self) -> None:
    """Signal is frozen — mutation raises FrozenInstanceError.

    Technique: Error Guessing — anticipating specific failure mode.
    """
```

### Common Techniques Reference

| Technique                     | When to Use                                    |
| ----------------------------- | ---------------------------------------------- |
| **Specification-based**       | Verifying contracts, constructors, interfaces  |
| **Equivalence Partitioning**  | Input domains with equivalent behavior classes |
| **Boundary Value Analysis**   | Numeric limits, timeouts, thresholds           |
| **Decision Table**            | Multiple conditions → outcomes mapping         |
| **State Transition**          | Lifecycle, connection states, FSMs             |
| **Branch/Condition Coverage** | Boolean expressions, if/else paths             |
| **Error Guessing**            | Anticipating specific failure modes            |
| **Round-trip Testing**        | Serialization/deserialization fidelity         |

## Test Structure

### AAA Pattern

Follow the Arrange-Act-Assert pattern:

```python
def test_parse_value_extracts_unit():
    # Arrange
    raw_value = "21.5 °C"

    # Act
    result = parse_value(raw_value)

    # Assert
    assert result == ("21.5", "°C")
```

### Naming Convention

Name tests descriptively: `test_<unit>_<behavior>_<condition>`

```python
def test_parse_value_extracts_unit_from_quantity(): ...
def test_signal_store_raises_not_found_for_invalid_id(): ...
def test_adapter_manager_schedules_retry_when_connection_fails(): ...
```

### Assertions

Use specific assertions, not generic truthy checks:

```python
# ❌ Bad - vague assertion
assert result

# ✅ Good - specific assertion
assert result.value == "21.5"
assert result.unit == "°C"
```

### Edge Cases

Always test critical edge cases:

- Empty inputs and None values
- Invalid data types
- Boundary conditions (use BVA)
- Error conditions and exceptions

## pytest Patterns

```python
# Use pytest fixtures for shared setup
@pytest.fixture
def sample_signal():
    return Signal(id="temp_1", value="21.5", unit="°C")


# Async tests auto-detected (asyncio_mode = "auto")
# No @pytest.mark.asyncio decorator needed!
async def test_store_publishes_to_subscribers():
    store = SignalStore()
    result = await store.get("test:id")
    assert result is None


# Use parametrize for equivalence classes
@pytest.mark.parametrize("input,expected", [
    ("21.5 °C", ("21.5", "°C")),   # Quantity type
    ("100 %", ("100", "%")),       # Percentage
    ("ON", ("ON", None)),          # Switch state
    ("UNDEF", (None, None)),       # Special value
])
def test_parse_value(input, expected):
    assert parse_value(input) == expected
```



## Test Organization

```
tests/
├── conftest.py              # Shared fixtures (signal_factory)
├── fixtures/                # Test data modules (import, don't conftest)
│   ├── openhab_responses.py
│   └── signals.py
├── unit/
│   ├── conftest.py          # Unit-specific (mock_adapter)
│   └── adapters/            # Mirror source structure
│       └── test_manager.py
└── integration/
    └── conftest.py          # Integration-specific (server startup)
```

- **Mirror source structure:** `test_manager.py` tests `manager.py`
- **Hierarchical conftest.py:** Scope fixtures to where they're needed
- **fixtures/ for data:** Import test data, don't put in conftest

## Sociable Unit Tests

Mock external I/O, use real lightweight dependencies:

```python
# ✅ Good: Real store, mocked adapter
async def test_manager_loads_signals(mock_adapter, signal_store):
    manager = AdapterManager()
    manager.add(mock_adapter)
    await manager.start_all()

    signals = await signal_store.get_all()
    assert len(signals) > 0


# ❌ Bad: Over-mocked, tests nothing
def test_store_set(mock_store):
    mock_store.set.return_value = None
    mock_store.set(signal)
    mock_store.set.assert_called_once()  # Proves nothing
```
