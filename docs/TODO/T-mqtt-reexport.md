# T-mqtt-reexport: Re-export MockMqttClient from cosalette.testing

**Phase trigger:** When building `cosalette.testing` module (Phase 6, `workspace-q7k`).

**Gate task:** `workspace-crg`

## Context

`MockMqttClient` is defined in `cosalette._mqtt` alongside the production classes
(`MqttClient`, `NullMqttClient`). Per ADR-007, test doubles should be available via
`cosalette.testing` so consumers don't need to import private modules.

## Decision Needed

When building `cosalette.testing`, re-export `MockMqttClient` (and any future mock
adapters like `MockMqttClient`) from the public testing API:

```python
# cosalette/testing/__init__.py
from cosalette._mqtt import MockMqttClient

__all__ = ["MockMqttClient", ...]
```

### Considerations

- **Keep `MockMqttClient` in `_mqtt.py`** — it's small and co-located with the protocol
  it implements, making maintenance straightforward.
- **Re-export, don't move** — moving would break internal imports and lose the
  co-location benefit.
- **Also re-export `NullMqttClient`?** — Evaluate whether `NullMqttClient` belongs in
  the testing module or stays production-only (useful for `--dry-run` mode).

## Outcome — RESOLVED (Phase 6, PR #11)

Both `MockMqttClient` and `NullMqttClient` are re-exported from
`cosalette.testing.__init__`. Gate task `workspace-crg` closed.

Decision: Re-export, don't move. `NullMqttClient` included in the testing
module since it's useful as a test double (silent no-op adapter).
