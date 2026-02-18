---
icon: material/text-box-outline
---

# Logging

Cosalette produces **structured JSON logs** for production and **human-readable
text logs** for development, with UTC timestamps, correlation metadata, and
zero external dependencies.

## Two Formats

=== "JSON (production)"

    ```json
    {"timestamp": "2026-02-14T12:34:56+00:00", "level": "INFO", "logger": "cosalette._app", "message": "MQTT connected to broker.local:1883", "service": "velux2mqtt", "version": "0.3.0"}
    ```

=== "Text (development)"

    ```
    2026-02-14 12:34:56,123 [INFO] cosalette._app: MQTT connected to broker.local:1883
    ```

The format is selected via `logging.format` in settings or `--log-format`
on the CLI:

```bash
myapp --log-format text   # development
myapp --log-format json   # production (default)
```

## NDJSON Format

JSON logs follow the **NDJSON** (Newline Delimited JSON) convention: one
complete JSON object per line, no embedded newlines. This is critical for
container log drivers (Docker, Kubernetes) that split on `\n`.

```python
def format(self, record: logging.LogRecord) -> str:
    entry = {
        "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
        "service": self._service,
    }
    if self._version:
        entry["version"] = self._version
    if record.exc_info and record.exc_info[0] is not None:
        entry["exception"] = self.formatException(record.exc_info)
    if record.stack_info:
        entry["stack_info"] = self.formatStack(record.stack_info)
    return json.dumps(entry, default=str)  # (1)!
```

1. `json.dumps` with `default=str` ensures even unexpected types (e.g.
   `pathlib.Path`) serialise without raising. Tracebacks are escaped
   by `json.dumps`, so multi-line exceptions become single-line JSON.

## UTC Timestamps

All timestamps are **UTC** in RFC 3339 / ISO 8601 format:

```
2026-02-14T12:34:56.789012+00:00
```

```python
datetime.fromtimestamp(record.created, tz=UTC).isoformat()
```

!!! info "Why UTC?"
    Container logs cross timezone boundaries — the host TZ may differ from
    the log aggregator's TZ. UTC removes ambiguity and lets the *display*
    layer apply local time when needed. This matches conventions in
    structured logging across ecosystems (Go `zap`, Rust `tracing`,
    Node `pino`).

## JSON Fields

| Field        | Type   | Always present | Description                              |
|--------------|--------|----------------|------------------------------------------|
| `timestamp`  | string | Yes            | ISO 8601 UTC timestamp                   |
| `level`      | string | Yes            | Python log level (`INFO`, `WARNING`, etc.) |
| `logger`     | string | Yes            | Dotted logger name (`cosalette._app`)    |
| `message`    | string | Yes            | Formatted log message                    |
| `service`    | string | Yes            | Application name (for log correlation)   |
| `version`    | string | When non-empty | Application version                      |
| `exception`  | string | When present   | Formatted traceback                      |
| `stack_info` | string | When present   | Stack trace (if `stack_info=True`)       |

### Correlation Metadata

Every log line includes `service` and `version`, enabling log aggregators to
filter and group entries without extra parser configuration:

```bash
# Loki query: all errors from velux2mqtt
{service="velux2mqtt"} |= "ERROR"

# Elasticsearch query: specific version
{"query": {"match": {"version": "0.3.0"}}}
```

## Custom Formatter Over python-json-logger

Cosalette implements its own `JsonFormatter` (~70 lines) rather than depending
on `python-json-logger`:

| Consideration          | Custom formatter           | python-json-logger        |
|------------------------|---------------------------|---------------------------|
| **Dependencies**       | Zero (stdlib only)        | One additional package    |
| **Field control**      | Full — matches project schema | Library defaults, overridable |
| **Container image**    | Smaller                   | Extra install step         |
| **Maintenance**        | Owned by project          | Third-party release cycle  |

!!! tip "Twelve-Factor App — Factor XI"
    "A twelve-factor app never concerns itself with routing or storage of its
    output stream." Cosalette logs to `stderr` and optionally to a rotating
    file. Log routing (to Loki, CloudWatch, etc.) is the platform's job.

## configure_logging()

The `configure_logging()` function is called once during Phase 1 (Bootstrap):

```python
configure_logging(
    settings.logging,    # LoggingSettings
    service="velux2mqtt",
    version="0.3.0",
)
```

It performs these steps:

1. **Clear existing handlers** on the root logger (prevents duplicate output)
2. **Build formatter** — `JsonFormatter` for `"json"`, `logging.Formatter` for `"text"`
3. **StreamHandler** → `stderr` (always installed)
4. **RotatingFileHandler** → optional, when `settings.logging.file` is set

### Text Format String

```python
_TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
```

Produces output like:

```
2026-02-14 12:34:56,123 [INFO] cosalette._app: Shutdown complete
```

### RotatingFileHandler

When `logging.file` is configured, logs are also written to a rotating file:

| Parameter      | Value                         |
|----------------|-------------------------------|
| `maxBytes`     | 10 MB                         |
| `backupCount`  | `settings.logging.backup_count` (default: 3) |
| `encoding`     | UTF-8                         |

The file handler uses the same formatter as the stream handler (JSON or text),
so file logs and stderr logs are always in the same format.

## Log Output Examples

=== "JSON — normal"

    ```json
    {"timestamp": "2026-02-14T12:34:56.789012+00:00", "level": "INFO", "logger": "cosalette._app", "message": "MQTT connected to broker.local:1883", "service": "velux2mqtt", "version": "0.3.0"}
    ```

=== "JSON — with exception"

    ```json
    {"timestamp": "2026-02-14T12:34:57.123456+00:00", "level": "ERROR", "logger": "cosalette._app", "message": "Device 'blind' crashed: Connection refused", "service": "velux2mqtt", "version": "0.3.0", "exception": "Traceback (most recent call last):\n  File \"_app.py\", line 268\n    ...\nConnectionRefusedError: Connection refused"}
    ```

=== "Text — normal"

    ```
    2026-02-14 12:34:56,789 [INFO] cosalette._app: MQTT connected to broker.local:1883
    ```

=== "Text — with exception"

    ```
    2026-02-14 12:34:57,123 [ERROR] cosalette._app: Device 'blind' crashed: Connection refused
    Traceback (most recent call last):
      File "_app.py", line 268
        ...
    ConnectionRefusedError: Connection refused
    ```

## Configuration Reference

```bash title=".env"
LOGGING__LEVEL=DEBUG       # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOGGING__FORMAT=json       # json or text
LOGGING__FILE=/var/log/myapp/myapp.log  # optional
LOGGING__BACKUP_COUNT=5    # rotated file generations
```

Or via CLI override:

```bash
myapp --log-level DEBUG --log-format text
```

CLI flags override environment variables and `.env` settings, following the
[configuration hierarchy](configuration.md).

---

## See Also

- [Configuration](configuration.md) — LoggingSettings and the configuration hierarchy
- [Error Handling](error-handling.md) — errors are logged at WARNING + published to MQTT
- [Lifecycle](lifecycle.md) — logging is configured in Phase 1 (Bootstrap)
- [ADR-004 — Logging Strategy](../adr/ADR-004-logging-strategy.md)
