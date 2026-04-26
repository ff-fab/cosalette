---
status: Accepted
date: 2026-04-25
impact: moderate
tags: [commands, mqtt, routing, error-handling]
---

# ADR-040: Command Sub-Dispatch

## Status

Accepted **Date:** 2026-04-25

## Context

Many IoT ecosystems — Home Assistant, Zigbee2MQTT, Tasmota — route multiple device operations through a **single MQTT topic** by embedding a routing field in the JSON payload:

```json
{"command": "on"}
{"command": "off"}
{"command": "set_brightness", "value": 128}
```

cosalette's `@app.command()` previously registered exactly **one handler per topic name**. Adapting to the single-topic pattern required writing a single handler that manually parsed the JSON, branched on the routing field, and delegated — mixing routing infrastructure with domain logic and forfeiting focused, independently-testable handlers.

The alternative of splitting operations across distinct MQTT topics (`cover/open/set`, `cover/close/set`) breaks ecosystem compatibility when downstream consumers expect `cover/set`.

The framework needed a first-class way to declare multiple handlers on the same topic, each owning one sub-command value, with routing, JSON parsing, and error publication handled automatically.

## Decision

Add `sub: str | None = None` and `sub_key: str = "command"` to `@app.command()` and `app.add_command()`. When `sub` is set, multiple handlers may share a topic name. The framework groups sub-dispatch handlers in `wire_router` and registers a single MQTT proxy per group. The proxy JSON-parses the payload, looks up `sub_key`, and routes to the matching handler — or publishes a structured error (`invalid_json`, `missing_sub_key`, `unknown_sub_command`). Registration-time guards prevent conflicting `sub_key` values and mixing of sub-dispatch and non-sub-dispatch handlers on the same topic.

```python
@app.command("cover", sub="open")
async def open_cover(payload: str) -> dict[str, object]:
    return {"position": 100}

@app.command("cover", sub="close")
async def close_cover(payload: str) -> dict[str, object]:
    return {"position": 0}

@app.command("cover", sub="set_position", sub_key="command")
async def set_position(payload: str) -> dict[str, object]:
    data = json.loads(payload)
    return {"position": data["value"]}
```

## Decision Drivers

- IoT ecosystems (HA, Zigbee2MQTT) commonly route operations via a single MQTT topic with a JSON routing field
- Each logical operation should live in its own focused, independently-testable handler
- Registration-time guards must make conflicting configurations impossible rather than silently wrong
- Structured error responses (not silent drops) are required when payloads are malformed or the routing field is unknown

## Considered Options

### Option 1: Manual mega-handler

Users write a single handler that JSON-parses the payload and dispatches internally with if/elif. No framework changes needed.

- *Advantages:* Zero framework complexity — no changes to registration or wiring; Full flexibility — user controls dispatch order and error handling
- *Disadvantages:* Mixes routing infrastructure with domain logic in every adapter; No registration-time safety — misconfigured routing fails silently at runtime; Structured error responses must be reimplemented per adapter; Individual operations cannot be tested in isolation without extracting helpers

### Option 2: Separate sub-command registry

Add a parallel `_sub_commands` list to `App` populated by `@app.command(sub=...)`. `wire_router` processes the two lists independently.

- *Advantages:* Regular command path is completely untouched — zero risk of regression; Sub-dispatch logic is isolated in a separate data structure
- *Disadvantages:* Fragments registration state into two lists, complicating `app.commands` and manifest generation; Deferred-enabled and NameSpec logic would need duplication; Two registration paths for conceptually the same thing (a command handler)

### Option 3: Metadata fields + grouping in wire_router (chosen)

Add `sub` and `sub_key` fields to `_CommandRegistration`. `wire_router` partitions commands into regular and sub-dispatch groups before registering proxies. One shared MQTT proxy is registered per sub-dispatch group; `run_command` is reused once routing succeeds.

- *Advantages:* Single unified command registry — `app.commands` returns all handlers consistently; Open/Closed: `run_command` is unchanged; new behaviour lives in a new `register_sub_command_proxy` method; `TopicRouter` is unchanged — still one handler per topic name; Deferred-enabled and NameSpec expansion work transparently; Registration guards are self-contained in `check_device_name`
- *Disadvantages:* `check_device_name` gains sub-dispatch guard logic, increasing cyclomatic complexity; Manifest entries for sub-dispatched commands differ structurally from regular commands

## Decision Matrix

| Criterion | Manual mega-handler | Separate sub-command registry | Metadata fields + grouping in wire_router |
| --- | --- | --- | --- |
| Developer ergonomics | 1 | 3 | 5 |
| Backward compatibility | 5 | 3 | 5 |
| Registration safety (guards at startup) | 1 | 4 | 5 |
| Error handling quality | 2 | 3 | 5 |
| Code duplication | 2 | 2 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Each operation lives in its own focused handler — domain logic is not mixed with routing
- Conflicting configurations are caught at startup (wrong `sub_key`, duplicate `sub` values, mixing sub/non-sub on the same topic)
- Structured error payloads (`invalid_json`, `missing_sub_key`, `unknown_sub_command`) are published automatically — no adapter boilerplate
- Regular commands are completely unaffected — full backward compatibility
- Manifest introspection exposes `sub` and `sub_key` for each handler

### Negative

- `check_device_name` carries additional branching for sub-dispatch guard logic
- Sub-dispatch manifest entries include `sub`/`sub_key` fields absent from regular command entries — consuming tools must handle both shapes

_2026-04-25_
