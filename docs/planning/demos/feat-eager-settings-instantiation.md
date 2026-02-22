# Eager Settings Instantiation

*2026-02-22T22:08:42Z by Showboat 0.6.0*
<!-- showboat-id: 644970fd-f0b1-4fe2-a5a6-c8e96242ccff -->

App.__init__ now eagerly instantiates settings_class(), exposing app.settings for decorator-time access. This fixes the correctness bug where env overrides for interval/enabled were silently ignored when using model_fields defaults.

```bash
cd /workspace && uv run python -c "from cosalette import App; a = App(name='demo', version='0.1.0'); print(f'app.settings type: {type(a.settings).__name__}'); print(f'mqtt.host: {a.settings.mqtt.host}')"
```

```output
app.settings type: Settings
mqtt.host: localhost
```

```bash
cd /workspace && uv run python -c "
from cosalette import App, Settings
a = App(name='demo', version='0.1.0')
# Use app.settings in decorator argument
@a.telemetry('sensor', interval=a.settings.mqtt.reconnect_interval)
async def sensor():
    return {'value': 1}
print(f'Registered telemetry interval: {a._telemetry[0].interval}')
"
```

```output
Registered telemetry interval: 5.0
```
