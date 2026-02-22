# OnChange Threshold Support

*2026-02-22T18:19:03Z by Showboat 0.5.0*

Implements OnChange(threshold=T) for global numeric dead-band and OnChange(threshold={field: T}) for per-field thresholds. Strict > comparison, bool excluded from numeric, NaN guards, negative threshold validation.

```bash
cd /workspace && uv run python -c "
from cosalette import OnChange
s = OnChange(threshold=0.5)
print('Above threshold (delta=1.0):', s.should_publish({'t': 21.0}, {'t': 20.0}))
print('At threshold (delta=0.5):', s.should_publish({'t': 20.5}, {'t': 20.0}))
print('Below threshold (delta=0.3):', s.should_publish({'t': 20.3}, {'t': 20.0}))
s2 = OnChange(threshold={'t': 0.5, 'h': 2.0})
print('Per-field above:', s2.should_publish({'t': 20.0, 'h': 63.0}, {'t': 20.0, 'h': 60.0}))
print('Per-field below:', s2.should_publish({'t': 20.0, 'h': 61.0}, {'t': 20.0, 'h': 60.0}))
"
```

```output
Above threshold (delta=1.0): True
At threshold (delta=0.5): False
Below threshold (delta=0.3): False
Per-field above: True
Per-field below: False
```
