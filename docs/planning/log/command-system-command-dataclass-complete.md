## Epic Command System Overhaul Complete: Command Dataclass and Queue

Added the `Command` frozen dataclass and internal command queue infrastructure
to `DeviceContext`, laying the foundation for the `ctx.commands()` async
iterator (COS-e7p.3) and sub-topic routing (COS-e7p.4).

**Files created/changed:**

- `packages/src/cosalette/_command.py`
- `packages/src/cosalette/_context.py`
- `packages/src/cosalette/__init__.py`
- `packages/tests/unit/test_command_dataclass.py`
- `packages/tests/unit/test_context.py`

**Functions created/changed:**

- `Command` dataclass (frozen, slotted) with `topic`, `payload`, `sub_topic`,
  `timestamp`
- `DeviceContext.__init__` — added `_command_queue` and `_commands_consumed`
  attributes

**Tests created/changed:**

- `TestCommand` — 7 tests (required fields, defaults, immutability, equality,
  hashability, full construction)
- `TestDeviceContextProperties.test_command_queue_exists` — verifies queue type
  and `_commands_consumed` initial state

**Review Status:** APPROVED

**Git Commit Message:**

```text
feat: add Command dataclass and per-device command queue

- Add frozen/slotted Command dataclass with topic, payload, sub_topic, timestamp
- Add internal asyncio.Queue[Command] and _commands_consumed flag to DeviceContext
- Export Command from cosalette public API
- Add 7 unit tests for Command + 1 for queue infrastructure
```
