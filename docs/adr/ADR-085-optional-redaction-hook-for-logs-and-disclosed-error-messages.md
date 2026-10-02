---
status: Proposed
date: 2026-10-02
impact: moderate
tags: [security, logging, error-handling]
---

# ADR-085: Optional redaction hook for logs and disclosed error messages

## Status

Proposed **Date:** 2026-10-02

## Context

ADR-061 keeps exception text off the broker unless the app lists the exception type in `disclose_messages_for`; undisclosed types publish only the class name. Two places still show raw exception text:

- **Disclosed messages.** A type the app discloses can still carry a token, a MAC address or a URL with credentials in some of its messages.
- **Local logs.** Runner log lines format the exception text and tracebacks (`exc_info`). Logs are shipped to aggregators that more people can read than the app's secrets.

Epic cos-4mv5 (proposal item P-7) asked for a framework hook so each app does not have to wrap its own handlers. Today an app can only work around it by adding its own logging filter after cosalette has configured logging. A filter on a logger does not apply to records from its child loggers, so a filter on the `cosalette` logger would miss app loggers and third-party libraries that log through the same handlers.

## Decision

Use an optional `App(redact=None)` hook, applied to disclosed error messages and to every record on the handlers cosalette installs, because one setting then covers both the broker and the local logs without changing ADR-061's disclosure decision.

- **Forms.** `redact` is `None` (default, no change), a callable `str -> str`, or an iterable of regular expressions (`str` or compiled `re.Pattern`). Each pattern match is replaced with `[REDACTED]`. Patterns are compiled and checked when the `App` is created; an invalid pattern raises `ValueError`, a wrong type raises `TypeError`.
- **Error payloads.** The redactor runs on the message of a disclosed error (`disclose_messages_for`, legacy map disclosure, and `verbose=True`). Undisclosed types still publish only the class name, unchanged (ADR-061).
- **Logs.** `configure_logging` installs a filter on each handler it creates (stderr and the optional rotating file). The filter redacts the formatted message, the formatted traceback and stack info on a copy of the record, so other handlers (for example a test's capture handler) still see the original. Installing it on the handlers rather than on the `cosalette` logger is a deliberate choice: logger filters do not reach child loggers, and app and library loggers write through the same handlers.
- **Failing redactor.** A redactor that raises is skipped for that text, which passes through unchanged, and logs one WARNING for the whole process. Redaction is best effort: it is a safety net on top of ADR-061, not a replacement.

```python
app = App(
    "airthings2mqtt",
    redact=[r"token=[^&\s]+", re.compile(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", re.I)],
)

# or a callable
app = App("velux2mqtt", redact=lambda text: text.replace(SECRET, "[REDACTED]"))
```

## Decision Drivers

- Secrets in disclosed messages and local logs must be removable with one setting
- ADR-061's disclosure decision must stay unchanged for undisclosed types
- The hook must cover app and library loggers, not only cosalette's own
- A broken redactor must not take logging or the app down

## Considered Options

### Option 1: Handler filter plus payload redaction (chosen)

Redact disclosed payload messages and install a filter on cosalette's handlers.

- *Advantages:* Covers every logger that writes through the app's handlers; Leaves the original record for other handlers
- *Disadvantages:* Handlers added by the app after configure_logging are not covered

### Option 2: Filter on the cosalette logger

Install the filter on the `cosalette` logger only.

- *Advantages:* Touches only framework records
- *Disadvantages:* Logger filters do not apply to child loggers, so most framework records would pass unfiltered; App and library loggers are never covered

### Option 3: No framework hook

Document the workaround: apps add their own logging filter and avoid disclosing risky types.

- *Advantages:* No new API
- *Disadvantages:* Every app repeats the same code; Disclosed error payloads cannot be redacted without forgoing disclosure

## Decision Matrix

| Criterion | Handler filter plus payload redaction | Filter on the cosalette logger | No framework hook |
| --- | --- | --- | --- |
| Coverage of log records | 5 | 1 | 3 |
| Coverage of disclosed payloads | 5 | 5 | 1 |
| Effort for app authors | 5 | 4 | 2 |
| Isolation from other handlers | 5 | 3 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- One setting removes known secret shapes from the broker and the logs
- Undisclosed types behave exactly as before (ADR-061)
- Redaction works on a copy, so test capture and other handlers see the original record

### Negative

- Every log record on cosalette's handlers pays for the redactor, including records that carry no secret
- A raising redactor fails open: the text it could not process is logged and published unredacted
- Handlers the app adds itself after configure_logging are not covered

_2026-10-02_
