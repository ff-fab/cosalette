# cosalette — Security Audit: Phase 0 Recon + Phase 1 Threat Model

**Audit date:** 2026-07-21 · **Branch:** `security/audit-2026-07-21` · **Scope:**
read-only recon. **Subject:** `cosalette` — opinionated Python framework for IoT-to-MQTT
bridge daemons (Python core + Rust filter crate + optional MCP server), published to
PyPI, deployed via Docker onto Raspberry Pi nodes.

> All secret material discovered on disk is referenced by name/type only and
> **redacted** in this document.

---

## 1. Package layout

```
/workspace
├── packages/src/cosalette/          # Python package (src layout, importable as `cosalette`)
│   ├── __init__.py                  # public API re-exports (__all__)
│   ├── __main__.py
│   ├── mqtt.py di.py filters.py persist.py stores.py strategies.py mcp.py   # PUBLIC facade modules
│   ├── testing/                     # public test harness (also a pytest11 plugin)
│   ├── _cli.py                      # per-App daemon CLI builder (build_cli)
│   ├── _package_cli/                # `cosalette` console-script (ai/schema/manifest/mcp subcommands)
│   ├── _mqtt/{_client,_router}.py   # MQTT adapter + topic router  (INGRESS)
│   ├── _runners/                    # command/telemetry/stream/periodic execution + _contracts.py (validation)
│   ├── _router/ _registration/ _wiring/ _context/ _injection.py   # routing / DI / registration
│   ├── _persistence/{_stores,_persist,_state}.py                  # Null/Memory/JsonFile/Sqlite stores
│   ├── _health/{_checker,_reporter}.py                            # health-check + auto-restart + LWT
│   ├── _errors.py _logging.py _json.py                            # error publish / JSON log / orjson
│   ├── _settings/                   # pydantic-settings (MqttSettings/LoggingSettings/Settings)
│   ├── _schema/                     # AsyncAPI 3.0 loader/validator/enforcement (optional [schema])
│   ├── _mcp/                        # FastMCP server + tools (optional [mcp])
│   ├── _cron/ _strategies/ _retry.py _clock.py _constants.py      # scheduling / publish strategies / retry
│   └── _ai_content/                 # downstream AI guidance text
├── packages/tests/{unit,integration,benchmarks,fixtures}/         # pytest suite
├── crates/cosalette-filters-rs/     # Rust crate → builds `cosalette._filters_rs` (maturin/pyo3, cdylib, abi3-py314)
│   └── src/{lib,median,one_euro,pt1,validation}.rs
├── docs/ (+ docs/adr/ ADR-001..049)  site/  zensical.toml         # Zensical docs + 49 ADRs
├── .devcontainer/Dockerfile          # ONLY Dockerfile (dev container; no prod image)
├── .github/workflows/                # ci, codeql, security, release-please, rust-wheels, docs, integration-tests
├── pyproject.toml Cargo.toml Cargo.lock uv.lock Taskfile.yml
└── .env(untracked) .env.example .secrets.baseline SECURITY.md
```

## 2. Public API surface

**`cosalette.__init__` `__all__`** (`packages/src/cosalette/__init__.py:111-218`)
exports downstream-facing symbols: `App`, `Router`, `Command`,
`AppContext`/`DeviceContext`/`SubEntityContext`, registration specs
(`CommandRegistration`, `DeviceRegistration`, `TelemetryRegistration`,
`StreamRegistration`, `PeriodicRegistration`, `CronSpec`/`IntervalSpec`/…),
`Settings`/`MqttSettings`/`LoggingSettings`, `SettingRef`/`setting_ref`, stores
(`Store`,`NullStore`,`MemoryStore`,`JsonFileStore`,`SqliteStore`,`DeviceStore`,`set_default_store_backend`),
filters (`Filter`,`MedianFilter`,`OneEuroFilter`,`Pt1Filter`), strategies,
retry/backoff/`CircuitBreaker`, persist policies, health
(`HealthReporter`,`HealthCheckable`,`build_will_config`,…), errors
(`ErrorPublisher`,`build_error_payload`), MQTT ports/clients, typed-contract types
(`Depends`,`Message`,`Payload`,`Topic`,
`PayloadValidationError`,`ReturnValidationError`), introspection helpers, logging
(`JsonFormatter`,`configure_logging`). **Public facade modules** (no leading
underscore): `cosalette.mqtt/di/filters/persist/stores/strategies/mcp` and
`cosalette.testing`.

**Entry points** (`pyproject.toml`):

- `[project.scripts] cosalette = cosalette._package_cli:main_cli` (`:45-46`) →
  subcommands `ai init|prime|help`, `ai mcp serve` (stdio only), `schema …`,
  **`manifest <module.path:attr>`**, `--version` (`_package_cli/__init__.py`).
- `[project.entry-points.pytest11] cosalette = cosalette.testing._plugin` (`:52-53`).
- Per-App **daemon CLI** built by `build_cli(app)` (`_cli.py:109`):
  `--dry-run/--version/--show-devices[-json]/ --log-level/--log-format/--env-file` +
  `schema` subcommands; invokes `App._run_async` (`_cli.py:100-102`).

## 3. PRIMARY trust boundary — inbound broker message → sink

Every hop with a `file:line`. **The framework boundary ends at the validated Python
object handed to the downstream handler; handler→adapter→hardware is downstream-defined
code.**

| #   | Stage                                                                                                                     | Location                                                                                                   |
| --- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| 1   | Broker delivers msg; `async for message in client.messages`                                                               | `_mqtt/_client.py:269`                                                                                     |
| 2   | `_dispatch(message)` — `topic = str(message.topic)`                                                                       | `_mqtt/_client.py:290-292`                                                                                 |
| 3   | **Payload decode** `message.payload.decode("utf-8")` — _no size limit_                                                    | `_mqtt/_client.py:301-305`                                                                                 |
| 4   | Fan-out to callbacks (router registered as callback)                                                                      | `_mqtt/_client.py:307-309`                                                                                 |
| 5   | `TopicRouter.route(topic, payload)`; raw topic **logged**                                                                 | `_mqtt/_router.py:85`, `:101`, `:111`                                                                      |
| 6   | Device extraction from attacker topic (longest-prefix)                                                                    | `_mqtt/_router.py:120-180`                                                                                 |
| 7   | Device proxy `_proxy` / sub-dispatch `_sub_proxy`                                                                         | `_runners/_command_runner.py:376`, `:447`                                                                  |
| 8   | Sub-dispatch `json.loads(payload)`; unknown-sub echoes `payload[:64]`                                                     | `_command_runner.py:459`, `:480-486`                                                                       |
| 9   | **Pydantic validation** `parse_payload`→`_decode_json`(orjson)→`_validate_python_value`                                   | `_runners/_contracts.py:193`, `:145`, `:159`                                                               |
| 9a  | Validation errors **sanitized** (no input echoed)                                                                         | `_runners/_contracts.py:181-190`                                                                           |
| 10  | **Handler invocation** `await reg.func(**kwargs)` (downstream code)                                                       | `_command_runner.py:224-225`                                                                               |
| 11a | Result → `publish_state({topic_base}/state)` (topic_base from validated name)                                             | `_command_runner.py:236-238`; `_context/_device_context.py:212`, `:149`                                    |
| 11b | Exception → `publish_error_safely` → `ErrorPublisher.publish` `message=str(error)` → `{prefix}/error`(+`/{device}/error`) | `_command_runner.py:265-266`; `_errors.py:106-114`, `:179-191`                                             |
| 12  | Persistence: store **key = registered validated name**, value = handler-return JSON                                       | `_runner_utils.py:26`; `_persistence/_stores.py:254-260` (SQLite, parameterized), `:202-207` (JSON atomic) |
| 13  | Adapter / hardware / OS / FS sink                                                                                         | **downstream app code (out of framework)**                                                                 |

### Points where untrusted broker data reaches a sink

- **Sink A — Logs (CWE-117):** raw inbound _topic_ → `logger.warning` at
  `_mqtt/_router.py:101`, `:111`; raw payload debug at `_mqtt/_client.py:295`. Safe
  under default JSON formatter (orjson escapes control bytes); **log-forgeable under
  `LoggingSettings.format="text"`** (`_logging.py:126`). Inbound topics are **not** run
  through `validate_mqtt_name` (that guards registration names only,
  `_registration/_validation.py:30`).
- **Sink B — Re-publish to shared MQTT topics (info leak / injection to co-tenants):**
  `str(exc)` of raw handler exceptions → `{prefix}/error` (`_errors.py:112` via
  `_command_runner.py:265`); `UnknownSubCommandError` echoes attacker `sub_value[:64]`
  (`_command_runner.py:482-486`). QoS 1, not retained — any subscriber to the error
  topic reads it.
- **Sink C — Pydantic validation** (`_runners/_contracts.py`): errors sanitized
  (`:181-190`) — **safe**.
- **Sink D — Persistence** (`_persistence/_stores.py`): SQLite parameterized
  (`:248`,`:257-260`); JSON via atomic temp+`os.replace` (`:202-207`); orjson decode
  with no object hooks. **Store key/path never derived from inbound topic** → no SQLi /
  path traversal from broker data. **Safe.** (State _values_ may embed payload-derived
  data but are JSON-encoded.)
- **Sink E — handler → adapter → hardware:** downstream-defined. Framework passes a
  **validated** typed object; the node-takeover / hardware-misuse surface lives in
  downstream handler + adapter code.

## 4. Component inventory (file:line)

- **MQTT client/config:** `MqttClient` `_mqtt/_client.py:29` (aiomqtt, lazy import
  `:226`; exp-backoff+jitter `:278-288`; TLS ctx `:187-198`; password via
  `SecretStr.get_secret_value` `:181-185`). Ports/doubles `_mqtt/__init__.py`
  (`MqttPort`,`MockMqttClient`,`NullMqttClient`,`WillConfig`). Config `MqttSettings`
  `_settings/__init__.py:44` (defaults **tls=False `:74`, port 1883 `:62`, user/pass
  None `:66-73`**; topic_prefix wildcard/NUL reject `:129-137`; TLS coherence validator
  `:139-162`).
- **Persistence:** `_persistence/_stores.py` — `NullStore:65`, `MemoryStore:87`,
  `JsonFileStore:128`, `SqliteStore:218`, `DeviceStore:276`. Policies
  `_persistence/_persist.py`; registration `_persistence/_state.py`. Default path
  `_app/_store_defaults.py:73` w/ traversal guard `:16`.
- **Scheduling:** intervals/periodic `_runners/_periodic.py`, `_app/_periodic.py`; cron
  (Quartz-style) `_cron/{_fields,_schedule,_special}.py`; sleep-until wall-clock
  `_clock.py`; strategies `_strategies/` (`Every`,`OnChange`,`All`,`Any`).
- **Health-check & auto-restart:** `_health/_checker.py` — `HealthCheckRunner:50`,
  `_probe:109` (timeout=interval/2), `_maybe_restart:199` (max_restarts, cooldown,
  sustained-reset). Reporter/LWT `_health/_reporter.py`; `build_will_config` (LWT
  availability).
- **Structured logging + error publishing:** `_logging.py` — `JsonFormatter:31` (emits
  `exception` traceback `:83-84`, `stack_info` `:86-87`), `configure_logging:92`
  (stderr + optional `RotatingFileHandler:135`). Error publish `_errors.py` —
  `build_error_payload:79` (`message=str(error):112`), `ErrorPublisher:122` →
  `{prefix}/error` `:179`.
- **Manifest / AsyncAPI generator:** `App.asyncapi()` (`_app/_asyncapi.py`);
  `cosalette manifest` `_package_cli/__init__.py:234`; AsyncAPI schema loader
  `_schema/_loader.py` (`yaml.safe_load:166`, ref-depth cap `_MAX_REF_DEPTH=50 :121`,
  cycle detect `:137`); validator/enforcement/consumer-gen/acl in `_schema/`.
- **Rust FFI boundary:** `crates/cosalette-filters-rs/src/lib.rs:13`
  `#[pymodule] _filters_rs` exposes `MedianFilter`/`OneEuroFilter`/`Pt1Filter`. **No
  `unsafe`/`extern "C"`/raw pointers.** Panic sources: two guarded `.unwrap()`
  `one_euro.rs:145-146`. Input guards `validation.rs:6 require_finite`,
  `one_euro.rs:9 reject_bool`. pyo3 0.29 `abi3-py314`, cdylib (`crates/.../Cargo.toml`).
- **MCP server tools:** `_mcp/_server.py:8 create_server_instance`; tool groups
  `_mcp/{_guidance,_adrs,_introspect_tools,_config,_scaffolding}.py`. All return
  **strings** (guidance/generated source/introspection) — no fs-write/exec/app-run tool.
  Import boundary `_mcp/_imports.py:25` (`importlib.import_module:43`). stdio-only
  (`_package_cli/__init__.py:182-187`).
- **Config / secret handling:** `pydantic-settings` `Settings`
  `_settings/__init__.py:237` (env `__` nesting, `.env`, `extra="ignore"` `:267-272`);
  MQTT password `SecretStr` `:70`; `SettingRef` `_settings/_ref.py`. `.env` (untracked),
  `.env.example`, `.secrets.baseline` (detect-secrets 1.5.0, `generated_at 2026-05-11`).
- **Docker / base images:** `.devcontainer/Dockerfile` only — base **digest-pinned**
  `python:3.14@sha256:9e51…` (`:5`), non-root `vscode`, all binaries SHA256-verified,
  Docker GPG fingerprint pinned (`:55`). **No production Dockerfile/compose.**
- **CI/CD workflows:** `.github/workflows/` — `ci.yml`
  (lint/unit/integration/complexity/**codeql**/security → `ci-gate`), `security.yml`
  (weekly `task security:audit`), `codeql.yml`, `release-please.yml`, `rust-wheels.yml`,
  `docs.yml`, `integration-tests.yml`, `devcontainer-build.yml`. Actions **full-SHA
  pinned**; `persist-credentials: false` everywhere; per-job least-privilege.
- **Release/publish pipeline:** `release-please.yml` — release-please (GitHub App token
  `:62-71`) → wheels (`rust-wheels.yml`, 8 targets) → SLSA provenance + CycloneDX SBOM
  (`:116-129`) → **TestPyPI then PyPI via OIDC Trusted Publishing** (`id-token: write`,
  `environment:` gates + manual approval, `:156-206`) → un-draft release (`:236-253`) →
  docs to Pages. **No long-lived PyPI token.**

## 5. Tooling / verification status (this session)

`task security:deps` → pip-audit **"No known vulnerabilities found"** (168 pkgs).
`cargo audit` → **exit 0, 15 crates, clean**. `.env` → **untracked & gitignored**
(`.gitignore:158`), `git log -- .env` empty. Available: pip-audit, cargo-audit, zizmor,
actionlint, syft, uv, task, cargo. **Missing: gitleaks, trufflehog, hadolint, trivy.**

---

# Phase 1 — Threat Model (system-specific)

**Attacker model.** The adversary (a) can **publish to the MQTT broker** — a compromised
broker, or a co-tenant client/app that shares the broker with a cosalette daemon —
**and** (b) can **author a downstream app** against the framework (a malicious or
careless framework consumer, or a supply-chain foothold in one). They do _not_ have a
shell on the Pi to start with; the goal is to _get_ one, misuse hardware, or poison the
fleet.

**Where REAL damage happens (ranked).**

1. **Fleet-wide compromise via a poisoned release (highest impact).** cosalette is one
   PyPI package fanned out to every Pi node. One malicious wheel/sdist, tag, or
   build-step = remote code execution on the entire fleet at `pip install`/deploy time.
   _Current posture is strong_: OIDC Trusted Publishing (no static token), SHA-pinned
   actions, SLSA provenance + attestations, TestPyPI→PyPI gating with environment
   approval, SBOM (`release-please.yml:116-206`), clean dependency audits. **Primary
   residual targets:** the release GitHub App credentials
   (`RELEASE_APP_ID`/`RELEASE_APP_PRIVATE_KEY`), the `update-security` job that pushes
   to `main` with that token (`release-please.yml:388-401`), Renovate auto-bumps, and
   the maturin/pyo3 build toolchain. Compromise here dwarfs any single-node bug.

2. **Node takeover via the import-on-untrusted-input boundary.**
   `cosalette manifest module:app` and the MCP config/introspect tools call
   `importlib.import_module(spec)` (`_mcp/_imports.py:43`), executing module-level code
   _before_ the `isinstance(App)` check. Any actor who can influence the spec string — a
   prompt-injected IDE agent driving the MCP server, a CI/tooling wrapper that passes
   attacker-derived input to `manifest`, or a downstream repo that ships a malicious
   module — gets code execution in the daemon/developer context. stdio-only transport is
   the sole mitigation; there is no allowlist/sandbox. **Highest-value single-node
   surface.**

3. **Hardware misuse & node DoS from the broker.** The inbound path is DoS-exposed:
   payloads are decoded and JSON-parsed with **no size/depth/rate limit**
   (`_mqtt/_client.py:301`, `_runners/_contracts.py:145`, `_command_runner.py:459`), so
   a co-tenant can exhaust CPU/memory on a small Pi or flood `{prefix}/+/set`. Beyond
   DoS, the framework's job ends at a _validated_ typed object handed to the downstream
   handler (`_command_runner.py:224`) — actual actuator/GPIO/serial writes live in
   downstream adapters, so **hardware misuse is only as safe as each app's Pydantic
   contract and handler logic.** The framework provides the validation engine and topic
   isolation but cannot itself prevent a permissive schema from turning a broker message
   into a physical action.

4. **Information disclosure to co-tenants on the shared broker.** Raw handler exception
   strings and up to 64 bytes of attacker-chosen payload are re-published to
   `{prefix}/error` / `{prefix}/{device}/error` at QoS 1 (`_errors.py:112`,
   `_command_runner.py:482`), readable by any other broker client. Combined with
   plaintext defaults (`tls=False`, port 1883, no auth — `_settings/__init__.py:62-74`)
   and examples that model unauthenticated MQTT, the default deployment posture assumes
   a trusted broker the attacker model says is _not_ trusted. Secondary: text-format
   logs are CWE-117 injectable via inbound topics (`_mqtt/_router.py:101/111` +
   `_logging.py:126`).

**Highest-value targets:** (i) release/CI signing identity & write-to-`main` automation;
(ii) the dynamic-import spec boundary; (iii) each node's downstream handler/adapter as
the physical-actuation gateway; (iv) the shared broker's confidentiality/authentication
posture. **Notable non-findings:** SQLite is parameterized, store paths are never built
from broker data, YAML uses `safe_load`, the Rust crate has no `unsafe`/FFI hazards, and
both dependency audits are clean — the sharp edges are the import boundary, broadcast
error content, missing ingress limits, and the deployment/broker defaults, **not** the
data-plane parsing or persistence internals.
