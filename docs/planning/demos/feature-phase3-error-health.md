# Phase 3: Error Publisher + Health Reporter

*2026-02-15T09:48:53Z by Showboat 0.5.0*

Added _errors.py (ErrorPayload, ErrorPublisher with pluggable type mapping, fire-and-forget MQTT publication per ADR-011) and _health.py (HealthReporter with heartbeat, device availability, LWT integration, graceful shutdown per ADR-012). 53 new tests, 100% coverage on both modules.

```bash
cd packages && uv run pytest tests/unit/test_errors.py tests/unit/test_health.py -q --tb=short 2>&1 | grep -oP '\d+ passed'
```

```output
53 passed
```

```bash
cd packages && uv run pytest tests/unit/ -q --tb=short 2>&1 | grep -oP '\d+ passed'
```

```output
144 passed
```
