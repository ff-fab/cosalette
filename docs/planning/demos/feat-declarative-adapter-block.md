# Declarative Adapter Block

*2026-02-26T18:51:04Z by Showboat 0.6.0*
<!-- showboat-id: 47153f4b-09fc-4edd-ae23-72aa42318c8a -->

Added adapters= dict parameter to App() constructor. Supports bare impl and (impl, dry_run) tuple forms. Delegates to self.adapter() for validation/registration. 12 tests cover tuple/bare forms, duplicates, coexistence, fail-fast, lifecycle, and invalid tuple length.

```bash
cd /workspace && uv run pytest packages/tests/unit/test_app.py -k 'TestDeclarativeAdapterBlock' --tb=no -q 2>&1 | grep -oP '\d+ passed'
```

```output
12 passed
```

```bash
cd /workspace && uv run pytest packages/tests/ --tb=no -q 2>&1 | grep -oP '\d+ passed'
```

```output
821 passed
```
