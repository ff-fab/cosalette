---
status: Accepted
date: 2026-10-12
impact: high
tags: [security, error-handling, mqtt, configuration]
---

# ADR-091: Labelling-Only Error Type Map and Secure-by-Default Message Disclosure

## Status

Accepted **Date:** 2026-10-12 | Supersedes ADR-061

## Context

ADR-061 added `disclose_messages_for` so that apps can assign an `error_type` label without publishing the exception message (audit finding F-DP1, CWE-209/532). It kept the legacy rule as the default: when `disclose_messages_for` is `None`, membership in the merged `error_type_map` implies disclosure. ADR-061 scheduled a default flip to labelling-only for 0.7.0. The project is at 0.11.x and that flip has not shipped, so the legacy coupling is still the default.

Under the current default:

- An app that maps a domain exception only to get a readable label also publishes its `str(error)` on the broker-visible `{prefix}/error` and `{prefix}/{device}/error` topics.
- The builtin `TimeoutError` is in the framework label map. The message of any `TimeoutError`, including one raised by library or application code, is therefore published. The ADR-061 amendment of 2026-10-12 records this as an accepted risk, pending this decision.
- Verbose error publishing (`MqttSettings.error_publish_verbose`, environment variable `MQTT__ERROR_PUBLISH_VERBOSE`) is controlled by the operator alone. It discloses every exception message process-wide, whatever the app has declared.

Since the ADR-061 amendment of 2026-10-12, the framework exports `cosalette.FRAMEWORK_DISCLOSED_ERRORS`. It contains the framework exceptions whose messages the framework builds and sanitises itself: `InvalidJsonError`, `MissingSubKeyError`, `UnknownSubCommandError` and `PayloadValidationError`. Apps can therefore list framework types in an explicit set without importing private modules.

## Decision

Make message disclosure on error topics secure by default and app opt-in only. The decision has six parts:

1. **`error_type_map` is labelling-only.** Mapping an exception type assigns its `error_type` label and never discloses its message. The rule that map membership implies disclosure is removed.
2. **Nothing is disclosed by default.** When `disclose_messages_for` is omitted, nothing is disclosed: `None` behaves the same as `frozenset()`. The error payload then carries the `error_type` label and the exception class name as `message`. This applies to every type, including the framework errors `InvalidJsonError`, `MissingSubKeyError`, `UnknownSubCommandError`, `PayloadValidationError` and `TimeoutError`.
3. **The explicit set is the only opt-in.** `disclose_messages_for` is the single source of truth for disclosure. A type's message is published only when that exact type is in the set. The set replaces the default and is not merged with anything. Apps that want the framework's sanitised messages pass `cosalette.FRAMEWORK_DISCLOSED_ERRORS | {MyError}`. Omitting the parameter already means "disclose nothing", so no opt-out sentinel exists.
4. **The builtin `TimeoutError` is disclosed only when listed.** It keeps the `timeout` label. Its message, including the framework watchdog message, is published only when the app lists `TimeoutError` in `disclose_messages_for`. `FRAMEWORK_DISCLOSED_ERRORS` does not contain it. This retires the accepted risk recorded in the ADR-061 amendment of 2026-10-12.
5. **Verbose error publishing requires app opt-in in code.** A new `App` parameter, `allow_error_publish_verbose: bool = False`, gates the operator setting `MqttSettings.error_publish_verbose` (`MQTT__ERROR_PUBLISH_VERBOSE`, default `False`). Verbose publishing is in effect only when both are true: `effective_verbose = allow_error_publish_verbose and settings.mqtt.error_publish_verbose`. If the operator enables the setting and the app does not allow it, the framework ignores the setting and logs one `WARNING` at startup. No framework default, preset or testing helper turns on either switch. Redaction (ADR-085) applies to every disclosed message, including messages published under verbose.
6. **Release.** This is a breaking change in 0.12.0, with no deprecation warning or deprecation period. The release notes carry the migration:
    - Pass `disclose_messages_for=cosalette.FRAMEWORK_DISCLOSED_ERRORS | {own types}` to keep messages.
    - Apps that map their own types in `error_type_map` no longer publish those messages unless they list the types.
    - Deployments that rely on `MQTT__ERROR_PUBLISH_VERBOSE` need the app to pass `allow_error_publish_verbose=True`.

    This replaces the 0.7.0 flip that ADR-061 scheduled.

```python
# Default: labels only. Every error publishes its label and class name.
app = cosalette.App(
    name="my-app",
    error_type_map={MyError: "my_error"},
)
# MyError -> error_type="my_error", message="MyError"
# PayloadValidationError -> error_type="invalid_payload",
#                           message="PayloadValidationError"

# Explicit opt-in: framework-sanitised messages plus one audited app type.
app = cosalette.App(
    name="my-app",
    error_type_map={MyError: "my_error"},
    disclose_messages_for=cosalette.FRAMEWORK_DISCLOSED_ERRORS | {MyError},
)

# Verbose publishing needs both the app opt-in and the operator setting
# MQTT__ERROR_PUBLISH_VERBOSE=true; redaction still applies.
app = cosalette.App(name="my-app", allow_error_publish_verbose=True)
```

## Decision Drivers

- Audit finding F-DP1 (CWE-209/532): exception messages on broker-visible error topics can carry hostnames, URLs, paths or credentials. Assigning a label must not publish a message as a side effect.
- Secure by default: messages and other details are disclosed only when the app opts in explicitly in code.
- A single source of truth for disclosure that apps can read and review in one place.
- The builtin `TimeoutError` is raised by library and application code with text that the framework does not control.
- The 0.7.0 default flip that ADR-061 scheduled has not shipped, and the project needs one current, authoritative rule.
- Breaking changes are acceptable in a 0.x minor release when the release notes carry a migration note (ADR-045, ADR-060 precedent).

## Considered Options

### Option 1: Labelling-only map, empty default, explicit set, app-gated verbose (chosen)

`error_type_map` assigns labels only. Omitting `disclose_messages_for` discloses nothing, framework errors included. Apps opt in through the explicit set (`FRAMEWORK_DISCLOSED_ERRORS | {...}`) with replace semantics. Verbose publishing requires `App(allow_error_publish_verbose=True)` plus the operator setting. Ships as a breaking change in 0.12.0 without deprecation.

- *Advantages:* No exception message reaches an error topic unless the app has listed its type or allowed verbose publishing; One parameter defines disclosure, which apps can review in a single place; The builtin TimeoutError text is published only when the app lists the type
- *Disadvantages:* Breaking change: by default, apps lose message output they get today; Apps that want framework messages must pass FRAMEWORK_DISCLOSED_ERRORS explicitly; Operators can no longer turn on verbose publishing without a code change in the app

### Option 2: Legacy coupling kept as the default

`disclose_messages_for=None` keeps meaning that membership in `error_type_map` implies disclosure, and the scheduled flip is dropped. Verbose publishing stays an operator-only setting.

- *Advantages:* No migration for existing apps; Framework and labelled app messages stay visible without extra configuration
- *Disadvantages:* Assigning a label still discloses the message unless the app passes an explicit set; Any builtin TimeoutError message is still published by default; Two rules for disclosure continue to coexist

### Option 3: Framework set disclosed by default

`error_type_map` becomes labelling-only. When `disclose_messages_for` is omitted, the framework discloses `FRAMEWORK_DISCLOSED_ERRORS`. An explicit set replaces that default.

- *Advantages:* Framework-sanitised messages stay visible with no configuration; App-mapped types stop disclosing by default
- *Disadvantages:* Default output still includes messages the app did not opt into; Changes to the framework set change what every app discloses by default

### Option 4: Merge semantics with an opt-out sentinel

Framework-sanitised types are always disclosed. The app set adds to them, and a dedicated sentinel suppresses framework disclosure.

- *Advantages:* Apps list only their own types to add disclosure; Framework messages stay visible without importing the framework set
- *Disadvantages:* Disclosure depends on two sources, so the app set alone no longer describes it; Needs an extra public sentinel, because an empty set can no longer mean 'disclose nothing'

## Decision Matrix

| Criterion | Labelling-only map, empty default, explicit set, app-gated verbose | Legacy coupling kept as the default | Framework set disclosed by default | Merge semantics with an opt-out sentinel |
| --- | --- | --- | --- | --- |
| Least-privilege disclosure by default | 5 | 1 | 3 | 3 |
| Single source of truth for disclosure | 5 | 2 | 3 | 2 |
| App control over verbose disclosure | 5 | 1 | 1 | 1 |
| Migration effort for existing apps | 2 | 5 | 3 | 3 |
| Public API surface | 4 | 5 | 5 | 3 |
| Builtin TimeoutError text withheld by default | 5 | 1 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- By default, error topics carry only labels and class names, which closes F-DP1 for every app that does not opt in.
- `disclose_messages_for` alone determines which messages are published, so reviewing disclosure means reading one parameter.
- Builtin `TimeoutError` text from library or application code is no longer published unless the app lists the type.
- Verbose publishing reflects a decision made in the app's code, and operators who set the variable without that opt-in get a startup WARNING instead of silent non-effect.
- The rule that ADR-061 left pending for 0.7.0 is settled with a single current statement.

### Negative

- Breaking change in 0.12.0 without a deprecation period. Apps that relied on labelled or framework messages publish class names until they add `disclose_messages_for`.
- Deployments that use `MQTT__ERROR_PUBLISH_VERBOSE` lose verbose output until the app passes `allow_error_publish_verbose=True`.
- Consumers that parsed framework error messages from error topics receive class names by default.
- Docs, AI help content and tests that describe the legacy rule must be updated together with the implementation.

_2026-10-12_
