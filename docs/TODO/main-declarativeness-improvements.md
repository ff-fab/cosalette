# Improving `main.py` Declarativeness

Goal: make `main.py` feel like a "FastAPI for MQTT" routes file — declarative, concise,
and intuitive. Since we maintain the cosalette framework and can still make breaking API
changes, improvements can land in either layer.

---

## 1. Adapter Lifecycle Protocol (Framework)

**Priority: HIGH — adds automatic adapter lifecycle management**

Add support for cosalette to auto-manage adapters that implement
`__aenter__`/`__aexit__` (or a simpler `initialize()`/`close()` lifecycle protocol).
When an adapter conforms to this convention, the framework calls its lifecycle methods
during startup/shutdown automatically — no explicit `lifespan=` hook needed.

The existing `lifespan=` hook remains fully supported for cases that need custom
orchestration (ordering constraints, multi-step init, non-adapter resources). The two
mechanisms coexist: auto-managed adapters run first, then the user-provided lifespan
hook (if any) wraps the application run phase as before.

### How `main.py` changes

```python
# BEFORE: explicit lifespan hook for adapter init/cleanup
@contextlib.asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    magnetometer = ctx.adapter(MagnetometerPort)
    magnetometer.initialize()
    try:
        yield
    finally:
        magnetometer.close()

app = cosalette.App(..., lifespan=lifespan)

# AFTER: adapter lifecycle auto-managed — lifespan hook no longer needed
# (but still available for custom orchestration if desired)
app = cosalette.App(...)
```

| Pros                                                    | Cons                                                           |
| ------------------------------------------------------- | -------------------------------------------------------------- |
| Removes ceremony for the common case (adapter init/close) | Implicit lifecycle may surprise users unfamiliar with the convention |
| Every adapter with init/cleanup benefits for free        | Adapter protocol needs a convention (context manager vs named methods) |
| `lifespan=` remains available for advanced use cases     | Edge case: adapters that must init in a specific order need the lifespan hook |
| Mirrors FastAPI's dependency lifecycle auto-management    | Two lifecycle mechanisms to document and understand             |

**Ecosystem note:** FastAPI moved from `@app.on_event("startup")` to `lifespan=`
context managers in 0.93+, partly because the lifecycle-per-dependency pattern was so
common. This is the same evolution — offer automatic management for the common case
while keeping the explicit hook for complex scenarios.

---

## 2. Settings-Aware Adapter Constructors (Framework)

**Priority: HIGH — eliminates factory boilerplate**

Allow `app.adapter()` to accept constructor kwargs or use DI on the adapter's
`__init__`, so cosalette auto-resolves Settings and passes them to the constructor.

### How `main.py` changes

```python
# BEFORE: factory function just to map settings → constructor args
def _make_magnetometer(settings: Gas2MqttSettings) -> Qmc5883lAdapter:
    return Qmc5883lAdapter(bus_number=settings.i2c_bus, address=settings.i2c_address)
app.adapter(MagnetometerPort, _make_magnetometer, dry_run=FakeMagnetometer)

# AFTER Option A: DI-injected adapter constructor (framework resolves Settings)
app.adapter(MagnetometerPort, Qmc5883lAdapter, dry_run=FakeMagnetometer)
# (Qmc5883lAdapter.__init__ type-hints settings: Gas2MqttSettings)

# AFTER Option B: explicit classmethod
app.adapter(MagnetometerPort, Qmc5883lAdapter.from_settings, dry_run=FakeMagnetometer)
```

| Pros                                                    | Cons                                                                     |
| ------------------------------------------------------- | ------------------------------------------------------------------------ |
| Removes 2 factory functions (8 lines + docstrings)      | Option A: couples adapter to settings type                               |
| Registration reads like a declaration                    | Option B: `from_settings` classmethod is boilerplate on the adapter side |
| Consistent with how `init=` already works on telemetry   | Conditional logic (`state_file is None → NullStorage`) doesn't fit DI    |

**Recommendation:** Option A with DI-injected constructors. The adapter already depends
on config values — making it explicit via type hints is cleaner than hiding it behind a
lambda.

---

## 3. Direct Function Registration (Framework)

**Priority: MEDIUM — removes the pass-through wrapper**

Allow `app.device(name)` (and other decorators) to accept an already-defined function
reference, like FastAPI's `app.add_api_route(path, endpoint)`.

### How `main.py` changes

```python
# BEFORE: thin wrapper
@app.device("gas_counter")
async def _gas_counter(ctx: cosalette.DeviceContext) -> None:
    await gas_counter(ctx)

# AFTER: direct registration
app.device("gas_counter")(gas_counter)
# or: app.add_device("gas_counter", gas_counter)
```

| Pros                                                    | Cons                                                         |
| ------------------------------------------------------- | ------------------------------------------------------------ |
| Removes 3-line wrapper per imported device               | Imperative `app.add_device()` reads differently than decorators |
| Devices defined in separate modules become first-class   | `app.device("gas_counter")(gas_counter)` is visually unusual |
| FastAPI does exactly this with `app.add_api_route()`     | Minor — just syntactic                                       |

**Teaching moment:** FastAPI offers both `@app.get("/")` (decorator) and
`app.add_api_route("/", handler)` (imperative). Having both is the standard pattern —
decorators for inline definitions, `.add_*()` for imported ones.

---

## 4. Conditional Device Registration via Settings Flag (Framework)

**Priority: MEDIUM — cleans up the `if settings.enable_debug_device:` block**

Add an `enabled=` parameter (accepting a bool or a callable) to `@app.telemetry()` /
`@app.device()`, so conditional registration is declarative.

### How `main.py` changes

```python
# BEFORE: imperative conditional
if settings.enable_debug_device:
    @app.telemetry("magnetometer", interval=settings.poll_interval)
    async def _magnetometer(...): ...

# AFTER Option A: enabled= parameter
@app.telemetry("magnetometer", interval=settings.poll_interval,
               enabled=settings.enable_debug_device)
async def _magnetometer(...): ...

# AFTER Option B: enabled= with callable (lazy evaluation)
@app.telemetry("magnetometer", interval=settings.poll_interval,
               enabled=lambda s: s.enable_debug_device)
async def _magnetometer(...): ...
```

| Pros                                                    | Cons                                                    |
| ------------------------------------------------------- | ------------------------------------------------------- |
| All devices visible in one flat list (no nesting)        | Adds a parameter to every decorator for a niche use case |
| Device still defined even when disabled (introspection)  | Option B with lambda is less readable than `if` block    |
| Mirrors FastAPI's `include_in_schema=` pattern           | Simple enough that `if` isn't really that bad            |

---

## 5. Declarative Adapter Block / `app.configure()` (Framework)

**Priority: LOW — aesthetic improvement**

Provide a grouped registration API, similar to FastAPI's `APIRouter` or a declarative
block:

```python
app = cosalette.App(
    name="gas2mqtt",
    version=__version__,
    settings_class=Gas2MqttSettings,
    adapters={
        MagnetometerPort: (Qmc5883lAdapter, FakeMagnetometer),
        StateStoragePort: (_make_storage_adapter, NullStorage),
    },
)
```

| Pros                                                    | Cons                                                       |
| ------------------------------------------------------- | ---------------------------------------------------------- |
| Everything visible in the constructor — truly declarative | Complex dict literal with tuples is less readable           |
| Mirrors Litestar's controller grouping                   | Harder to type-check; loses docstring opportunity           |
| Smaller `create_app()` body                              | Non-standard pattern for Python DI                         |

---

## 6. Event-Driven Telemetry Variant (Framework)

**Priority: LOW (high value but high effort)**

Add `@app.telemetry` variant that publishes on state change rather than on a fixed
interval. The gas counter's poll loop + Schmitt trigger + conditional publish could
become:

```python
@app.telemetry("gas_counter", interval=settings.poll_interval,
               publish=OnTrigger())  # only publishes when return value changes
```

| Pros                                                    | Cons                                                          |
| ------------------------------------------------------- | ------------------------------------------------------------- |
| Gas counter could become a simple telemetry function     | Schmitt trigger + counter + persistence + commands = too complex |
| Removes the biggest `@app.device` in the app             | The gas counter genuinely needs `@app.device`                  |

**Verdict:** The gas counter is irreducibly stateful — this won't simplify it
meaningfully.

---

## Priority Summary

| #   | Idea                          | Layer     | Impact     | Effort | Priority   |
| --- | ----------------------------- | --------- | ---------- | ------ | ---------- |
| 1   | Adapter lifecycle protocol    | Framework | **High**   | Medium | **HIGH**   |
| 2   | Settings-aware adapter DI     | Framework | **High**   | Medium | **HIGH**   |
| 3   | Direct function registration  | Framework | **Medium** | Low    | **MEDIUM** |
| 4   | Conditional `enabled=` param  | Framework | **Medium** | Low    | **MEDIUM** |
| 5   | Declarative adapter block     | Framework | **Low**    | Medium | **LOW**    |
| 6   | Event-driven telemetry        | Framework | **Low**    | High   | **LOW**    |

---

## Vision: `main.py` After Improvements #1–#4

```python
"""gas2mqtt application entry point."""
from gas2mqtt import __version__
from gas2mqtt.adapters.fake import FakeMagnetometer, NullStorage
from gas2mqtt.adapters.json_storage import JsonFileStorage
from gas2mqtt.adapters.qmc5883l import Qmc5883lAdapter
from gas2mqtt.devices.gas_counter import gas_counter
from gas2mqtt.ports import MagnetometerPort, StateStoragePort
from gas2mqtt.settings import Gas2MqttSettings

import cosalette
from cosalette import OnChange, Pt1Filter

app = cosalette.App(
    name="gas2mqtt",
    version=__version__,
    description="Domestic gas meter reader via QMC5883L magnetometer",
    settings_class=Gas2MqttSettings,
)

settings: Gas2MqttSettings = app.settings  # type: ignore[assignment]

# Adapters (lifecycle auto-managed by framework)
app.adapter(MagnetometerPort, Qmc5883lAdapter, dry_run=FakeMagnetometer)
app.adapter(StateStoragePort, _make_storage_adapter, dry_run=NullStorage)

# Devices
app.add_device("gas_counter", gas_counter)

def _make_pt1(settings: Gas2MqttSettings) -> Pt1Filter:
    return Pt1Filter(tau=settings.smoothing_tau, dt=settings.temperature_interval)

@app.telemetry("temperature",
               interval=settings.temperature_interval,
               publish=OnChange(threshold={"temperature": 0.05}),
               init=_make_pt1)
async def _temperature(mag: MagnetometerPort, s: Gas2MqttSettings, pt1: Pt1Filter):
    raw = s.temp_scale * mag.read().temperature_raw + s.temp_offset
    return {"temperature": round(pt1.update(raw), 1)}

@app.telemetry("magnetometer",
               interval=settings.poll_interval,
               enabled=settings.enable_debug_device)
async def _magnetometer(mag: MagnetometerPort):
    r = mag.read()
    return {"bx": r.bx, "by": r.by, "bz": r.bz}
```

~40 lines vs the current ~128 — a **~70% reduction**, with zero `create_app()`
function, no lifespan hook needed, no adapter factory functions, and every device
visible at the top level.
