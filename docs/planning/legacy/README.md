# Legacy Reference Scripts

These are real-world scripts from the user's existing smart home setup.
They represent the **before** state — what cosalette-based projects will replace.

## Files

### hmc5883.py — Gas Counter (Telemetry Archetype)

**Project:** gas2mqtt
**Hardware:** HMC5883 magnetometer via I²C (smbus2), Raspberry Pi
**Pattern:** Synchronous polling loop → MQTT publish
**Data direction:** Unidirectional (sensor → MQTT)

Key observations for framework extraction:

- Uses `paho-mqtt` (synchronous) — cosalette will use `aiomqtt` (async)
- Hardcoded MQTT credentials and broker address
- Custom logging with file rotation (identical boilerplate to wizcontrol.py)
- Hysteresis-based trigger detection (domain logic — stays in project)
- EWMA temperature filtering (domain logic — stays in project)
- `time.sleep(1)` polling loop — cosalette provides `ctx.sleep()`

### wizcontrol.py — Smart Lights (Command & Control Archetype)

**Project:** wiz2mqtt
**Hardware:** WiZ smart bulbs via WiFi/UDP (pywizlight library)
**Pattern:** MQTT subscribe → parse command → device action → publish state
**Data direction:** Bidirectional

Key observations for framework extraction:

- Uses `aiomqtt` — same library cosalette will wrap
- Reconnection loop with `while True` / `try/except` (framework provides this)
- Topic parsing via regex `wiz/(.*?)/` (framework's TopicRouter handles this)
- `actual` topic filtering to prevent feedback loops (framework handles this)
- Multiple bulbs grouped into logical "lights" (multi-device config pattern)
- Hardcoded device configuration as Python dict (cosalette uses pydantic-settings)
- No error handling for device commands (cosalette's ErrorPublisher handles this)
- Identical logging boilerplate to hmc5883.py
