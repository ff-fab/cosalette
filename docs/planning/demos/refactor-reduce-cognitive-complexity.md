# Reduce Cognitive Complexity Below 15

*2026-03-05T18:00:38Z by Showboat 0.6.1*
<!-- showboat-id: 0b571595-e7c9-402e-ba35-8086c461ffb7 -->

Refactored all 4 functions exceeding cognitive complexity threshold 15. Extracted 11 helpers across _app.py and _cli.py. All noqa suppressions removed — the gate now enforces without exceptions.

```bash
task complexity:cognitive 2>&1 | head -5
```

```output
task: [complexity:cognitive] uv run flake8 --select=CCR001 packages/src/cosalette/
```

```bash
grep -c 'noqa: CCR001' packages/src/cosalette/_app.py packages/src/cosalette/_cli.py 2>&1 || true
```

```output
packages/src/cosalette/_app.py:0
packages/src/cosalette/_cli.py:0
```

```bash
task test:unit 2>&1 | tail -1 | sed 's/ in [0-9.]*s//' 
```

```output
============================= 854 passed ==============================
```
