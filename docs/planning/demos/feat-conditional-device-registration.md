# Conditional Device Registration

*2026-02-26T18:10:47Z by Showboat 0.6.0*
<!-- showboat-id: e918c77d-6857-4ba4-b170-483fc5ed142c -->

Added enabled= parameter to all 6 device registration methods. When enabled=False, registration is silently skipped — no entry in the registry, no name reservation, no validation. 17 new tests cover all paths including root devices, persist validation, and runtime exclusion.

```bash
cd /workspace && uv run pytest packages/tests/unit/test_app.py -k 'TestConditionalRegistration' --tb=no -q 2>&1 | grep -oP '\d+ passed'
```

```output
17 passed
```

```bash
cd /workspace && uv run pytest packages/tests/ --tb=no -q 2>&1 | grep -oP '\d+ passed'
```

```output
809 passed
```
