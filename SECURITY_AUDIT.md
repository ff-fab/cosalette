# Cosalette Security Audit — 2026-07-21

Branch: `security/audit-2026-07-21` · Method: static analysis + targeted PoCs (no live
broker/hardware, no dynamic fuzzing). Scope: Python IoT→MQTT bridge framework, Rust
filter crate (`crates/cosalette-filters-rs`), optional stdio MCP server, CI/CD,
Docker/deploy guidance, and secret hygiene.

---

## 1. Executive summary

**Attacker model (one line):** an unauthenticated network peer who can publish to the
shared MQTT broker (the framework's default posture is **no-auth plaintext**, port
1883), and/or a party who can author a downstream app or feed input to a developer tool
— plus, for the MCP surface, a prompt-injected coding agent running on a developer
laptop.

**The two High findings:**

- **MQTT-01 (High, 7.5) — FIXED.** A single non-UTF-8 MQTT payload to any subscribed
  `set` topic crashed the dispatch decode, which the connection loop mistook for a
  connection loss → teardown + reconnect backoff. Sent **retained**, the broker
  redelivers it on every re-subscribe, producing a **self-sustaining, restart-surviving
  crash-loop** with no further attacker action. Fleet-wide. Trivially exploitable by any
  co-tenant. Confirmed with PoC. **Now guarded.**
- **MCP-01 (High, 8.4) — LEFT FOR REVIEW.** 8 of 16 MCP tools import a client-controlled
  `module:attr` spec, executing module top-level code at `importlib.import_module`
  **before** the `isinstance(App)` check. A prompt-injected coding agent calling e.g.
  `cosalette_inspect_app("evil_module:app")` (repo root on `sys.path`) → **RCE as the
  developer** → poisoned commit → fleet. The fix is a behavior-changing design decision
  (import allowlist / opt-in env gate), so it is described here, not committed.

**Overall posture.** The framework is **well-engineered on the dimensions that usually
fail**, and the serious issues cluster at two trust boundaries (the raw MQTT ingress and
the dynamic-import developer tooling). Genuinely strong areas, verified this session:

- **CI/CD** — 100% of `uses:` are 40-hex SHA-pinned or local `./`; least-privilege
  top-level `contents:read` with narrowly-scoped elevation; OIDC Trusted Publishing (no
  static PyPI token); SLSA provenance + PEP-740 attestations + CycloneDX SBOM;
  injection-safe workflows. Residuals are two supply-chain hardening nits (CICD-01/02).
- **Rust FFI** — zero `unsafe`, zero raw pointers/FFI; `panic=unwind` + pyo3 0.29
  `catch_unwind` contains panics as Python exceptions (not a daemon abort). The only two
  Rust findings are numeric-robustness nits (RUST-02 fixed, RUST-03 described).
- **Persistence** — SQLite fully parameterized; store keys never derived from broker
  data; no unsafe deserialization (orjson, no object hooks); traversal guards on the
  default store path.
- **Secret hygiene** — no secrets in the tracked tree or in 772 commits of history
  (gitleaks + detect-secrets, clean); lockfiles committed and hash-pinned (uv.lock 768
  sha256 all from PyPI, Cargo.lock all crates.io-checksummed); no git/url/path deps.
- **Framework's own error text is sanitized** — validation/serialisation/JSON-decode
  errors never echo payload bytes to the error topic (LEAK-01 is strictly about
  _downstream_ exception content the framework rebroadcasts).

**Fixed this session (6 atomic commits):** MQTT-01, MQTT-03, MCP-02, MCP-03, RUST-02,
CICD-02. **Left for human review (risky/judgment):** MCP-01, IMP-01, LEAK-01, MQTT-02,
CICD-01, PERS-01, RUST-03, DOCK-01..04, SEC-01, DEP-01 (CI check), DEP-02.

---

## 2. Findings table (sorted by real risk)

| ID      | Title                                                        | Sev  | CVSS         | CWE         | Conf.              | Blast radius                 | Status                     |
| ------- | ------------------------------------------------------------ | ---- | ------------ | ----------- | ------------------ | ---------------------------- | -------------------------- |
| MQTT-01 | Non-UTF-8 payload → dispatch crash → retained crash-loop DoS | High | 7.5          | 20/248/400  | Confirmed (PoC)    | Fleet-wide                   | **Fixed**                  |
| MCP-01  | 8/16 MCP tools import client-controlled `module:attr` → RCE  | High | 8.4          | 94 via 829  | Confirmed          | Dev laptop → fleet           | Left-for-review            |
| IMP-01  | `manifest`/`schema` CLIs import spec before validating       | Med  | 7.8 (impact) | 94/706      | Confirmed (PoC)    | Dev/CI                       | Left-for-review (doc)      |
| CICD-01 | Renovate auto-merges minor/patch actions + deps              | Med  | 7.2→Med      | 829/1357    | Confirmed (config) | Fleet-wide                   | Left-for-review            |
| LEAK-01 | Raw downstream exception strings broadcast to error topics   | Med  | 5.9          | 209/201     | Confirmed (PoC)    | Fleet-wide                   | Left-for-review            |
| MQTT-02 | No inbound payload size cap → OOM on constrained nodes       | Med  | 5.9          | 770/400     | Suspected          | Fleet-wide                   | Left-for-review            |
| CICD-02 | Unpinned `pip install maturin` in contents:write job         | Low  | 5.9          | 1104/829    | Confirmed          | main → fleet                 | **Fixed**                  |
| DOCK-03 | Guide offers `privileged: true` as device-access fix         | Low  | 6.0          | 250/269     | Confirmed          | Single node (host root)      | Left-for-review            |
| DOCK-02 | Reference compose ships `changeme` plaintext broker password | Low  | 5.0          | 798/1188    | Confirmed          | Single node                  | Left-for-review            |
| DEP-01  | Installed venv drifted to mcp 1.27.0 (3 CVEs, unreachable)   | Low  | 4.7          | 1104/346    | Confirmed          | Dev/CI (unreachable)         | Left-for-review (CI check) |
| SEC-01  | Pre-commit is PEM-only; no detect-secrets; `.env` un-ignored | Low  | 4.2          | 1188/522    | Confirmed          | Single repo                  | Left-for-review            |
| MQTT-03 | Log injection via unvalidated inbound topic (text format)    | Low  | 4.0          | 117         | Confirmed (PoC)    | Fleet-wide (low impact)      | **Fixed**                  |
| DOCK-01 | Primary reference Dockerfile/compose unhardened by default   | Low  | 3.9          | 1188/16     | Confirmed          | Per-node → fleet             | Left-for-review            |
| RUST-02 | MedianFilter window unbounded → alloc abort() kills daemon   | Low  | 3.7          | 770/789/197 | Suspected          | Node (if untrusted `window`) | **Fixed**                  |
| RUST-03 | OneEuroFilter non-finite internals → permanent NaN           | Low  | 3.7          | 682/697     | Confirmed mech.    | Node (if untrusted input)    | Left-for-review            |
| MCP-02  | `config_schema` leaks hard-coded secret defaults to LLM      | Low  | 3.3          | 215/200     | Confirmed (PoC)    | Dev laptop                   | **Fixed**                  |
| PERS-01 | State files created at process umask (world-readable)        | Low  | 3.3          | 732         | Suspected          | Single node (multi-user)     | Left-for-review            |
| DOCK-04 | Devcontainer `COPY --from` stages pinned to mutable tags     | Low  | 2.6          | 1104/829    | Confirmed          | Dev/CI                       | Left-for-review            |
| MCP-03  | `scaffold_adapter` interpolates unvalidated free-text        | Low  | 2.5          | 94          | Confirmed          | Dev laptop                   | **Fixed**                  |
| DEP-02  | `mcp` extra pulls MCP SDK fully transitively/unpinned        | Info | —            | 1357        | Confirmed          | Fleet (policy)               | Left-for-review            |
| IMP-02  | AsyncAPI generator — no eval/exec/SSTI                       | Info | —            | —           | Confirmed          | —                            | Assurance                  |
| RUST-01 | Rust FFI memory-safe + panic-contained                       | Info | —            | —           | Confirmed          | —                            | Assurance                  |
| DEP-03  | Lockfiles committed + hash-pinned, no git/url/path deps      | Info | —            | —           | Confirmed          | —                            | Assurance                  |
| LEAK-02 | UnknownSubCommand reflects ≤64B of attacker's own input      | Info | 0.0          | 209         | Confirmed          | —                            | Assurance                  |
| SEC-02  | Working-tree `.env` has live-looking dev secrets (protected) | Info | 1.6          | 312         | Confirmed          | Single dev env               | Assurance (rotate)         |
| SEC-03  | `.secrets.baseline` stale (one orphaned moved-file entry)    | Info | —            | 1188        | Confirmed          | Tooling                      | Left-for-review            |

---

## 3. Per-finding detail

### MQTT-01 — Non-UTF-8 payload crashes dispatch → retained crash-loop DoS — **FIXED**

- **Severity/CVSS:** High — `CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H` = 7.5 (PR:N
  because the default posture requires no broker auth; with auth → PR:L → 6.5).
- **CWE:** 20 (Improper Input Validation) + 248 (Uncaught Exception) + 400 (DoS).
- **Location:** `packages/src/cosalette/_mqtt/_client.py` `_dispatch` (the unguarded
  `message.payload.decode("utf-8")`), vs. the per-callback guard immediately below;
  propagation via the `async for message in client.messages` loop into the connection
  loop's `except Exception` reconnect/backoff; re-subscribe on reconnect (`subscribe`
  loop, qos=1).
- **Attack path (PoC `scratchpad/poc_dispatch_dos.py`):** publish `b"\xff\xfe"` to any
  subscribed `{prefix}/{device}/set`. `.decode("utf-8")` raises `UnicodeDecodeError`
  _before_ the guarded loop → escapes `_dispatch` → caught by the connection loop as if
  the connection dropped → teardown + exponential backoff (default up to 300 s). Sent
  `retain=True`, the broker redelivers on every re-subscribe → connect → poison → crash
  → backoff → reconnect → crash … **forever, surviving restarts**, with no further
  attacker action. LWT shows the node offline fleet-wide.
- **Confidence:** Confirmed (PoC).
- **Blast radius:** Fleet-wide — every node running the production `MqttClient`.
- **Remediation (applied):** wrap the decode in `try/except UnicodeDecodeError`, log a
  warning with the topic rendered via `%r`, and `return` — mirroring the existing
  None-payload skip. Non-UTF-8 is already invalid input for the JSON/str handlers, so
  dropping it changes no legitimate behavior. Regression test added
  (`test_skips_non_utf8_payload`) proving the payload no longer escapes `_dispatch`.

### MCP-01 — Dynamic import of client-controlled spec → RCE — **LEFT FOR REVIEW**

- **Severity/CVSS:** High — `CVSS:3.1/AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H` = 8.4.
- **CWE:** 94 (Code Injection) via 829 (Inclusion of Functionality from Untrusted
  Control Sphere).
- **Location:** `packages/src/cosalette/_mcp/_imports.py` (`import_module` runs module
  top-level code before the `isinstance(App)` check). Reached by `_introspect_tools.py`
  (multiple call sites), `_config.py`, `_scaffolding.py` — 8 of 16 registered MCP tools.
- **Attack path:** a prompt-injected coding agent (or malicious repo content the agent
  processes) invokes e.g. `cosalette_inspect_app("evil_module:app")`. With the repo root
  on `sys.path`, `import_module("evil_module")` executes attacker code as the developer.
  The `isinstance` "validation" runs only _after_ import, so it is not a control.
- **Confidence:** Confirmed.
- **Blast radius:** developer laptop → poisoned commit → fleet.
- **Why left for review:** any real fix is behavior-changing (an env-gated import
  allowlist, or opt-in `COSALETTE_MCP_ALLOW_IMPORT`) and must not break the legitimate
  "point the tool at my app" workflow. **Decision needed:** default-deny with an opt-in
  env var, vs. an allowlist of importable module prefixes. The only trivial part —
  aligning the tool docstrings/README so they stop implying the tools "only return
  strings" (literally true, but they import arbitrary modules as a side effect) — can be
  done independently.

### IMP-01 — `manifest`/`schema` CLIs import before validating — **LEFT FOR REVIEW (doc)**

- **Severity/CVSS:** Med — impact `CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H` = 7.8.
- **CWE:** 94 / 706 (Use of Incorrectly-Resolved Name or Reference).
- **Location:** `_mcp/_imports.py`; `_package_cli/__init__.py`;
  `_schema/_cli_helpers.py`; `_schema/_cli.py`; `_utils.py` →
  `_wiring/_adapter_lifecycle.py`.
- **Attack path:** `cosalette manifest module:app` / `schema` import the spec before
  validating. The spec is **developer-controlled** — an inherent, documented dev-tool
  boundary (identical to `uvicorn/gunicorn module:app`). It becomes a genuine vuln only
  when untrusted input reaches the spec (evaluating a hostile repo/PR, or a CI wrapper
  that derives the spec from untrusted data). No auto-discovery exists.
- **Confidence:** Confirmed (PoC).
- **Remediation:** document the boundary honestly (do **not** present the post-import
  `isinstance` check as a security control). Real sandboxing is risky and behavior-
  changing. A concise doc note is the safe part.

### CICD-01 — Renovate auto-merges minor/patch actions + deps — **LEFT FOR REVIEW**

- **Severity/CVSS:** Med (base 7.2, tempered by compensating controls) —
  `AV:N/AC:H/PR:H/ S:C/C:H/I:H/A:H`.
- **CWE:** 829 / 1357 (supply chain).
- **Location:** `renovate.json` — `github-actions` group `automerge:true`, `uv`/deps
  `automerge:true`, `pre-commit` `automerge:true`; majors + Dockerfile are manual.
- **Attack path:** an attacker compromises an upstream action/dep and ships a malicious
  minor/patch; Renovate opens and auto-merges to `main` if CI passes. The release path
  holds `id-token`/`contents` write, so a poisoned action could exfiltrate the PyPI OIDC
  token or tamper the wheel. SHA-pinning protects steady state; auto-merge is exactly
  the window that should be gated.
- **Compensating controls:** actions SHA-pinned, CI-gated, majors + Dockerfile manual,
  pip/cargo-audit in CI.
- **Why left for review:** removing automerge changes the maintainer's workflow.
  **Recommendation:** `automerge:false` for the `github-actions` group; add
  `minimumReleaseAge: "3 days"` + `internalChecksFilter: "strict"` to the uv/pre-commit
  groups.

### LEAK-01 — Raw downstream exception strings broadcast to error topics — **LEFT FOR REVIEW**

- **Severity/CVSS:** Med — `CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N` = 5.9.
- **CWE:** 209 (Error Message with Sensitive Info) + 201 (Insertion of Sensitive Info
  into Sent Data).
- **Location:** `_errors.py` (`message=str(error)`) published to `{prefix}/error` and
  `{prefix}/{device}/error` (QoS 1); reached from `_command_runner.py` and
  `_runner_utils.py` `publish_error_safely`.
- **Attack path (PoC `scratchpad/poc_leak_and_depth.py` §d):** a downstream handler that
  talks to a DB/API/serial device raises an exception whose text embeds a
  credential/host/token (common for driver/HTTP libraries). The framework re-publishes
  `str(exc)` unredacted to broker-wide error topics; on the default unauthenticated
  broker any co-tenant subscribing `{prefix}/#` harvests it fleet-wide. **Note:** the
  framework's _own_ error text is already sanitized — this is specifically _downstream_
  exception content the framework rebroadcasts.
- **Confidence:** Confirmed (PoC).
- **Why left for review:** behavior-changing — publishing only `error_type` + a generic
  message by default removes remote diagnostic detail operators may rely on. **Decision
  needed:** default to type-only with an explicit opt-in flag for verbose `str(error)`,
  and document that error-topic content is broker-visible.

### MQTT-02 — No inbound payload size cap → OOM — **LEFT FOR REVIEW**

- **Severity/CVSS:** Med — `CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:H` = 5.9.
- **CWE:** 770 / 400.
- **Location:** `_mqtt/_client.py` (full payload decoded to `str`, ~2×), then
  `_runners/_contracts.py` (orjson) / `_command_runner.py` (stdlib json). No size guard
  in `_mqtt/` or `_settings/`.
- **Attack path:** a co-tenant publishes a single very large payload (tens–hundreds of
  MB) to a `set` topic; paho/aiomqtt buffers the whole message, `_dispatch` decodes it,
  the runner JSON-parses it — several multiples of raw size resident at once. On a 512
  MB–1 GB Pi this can OOM-kill the daemon.
- **Confidence:** Suspected (code fact confirmed — no guard anywhere; OOM depends on
  broker `message_size_limit` and node RAM, not directly reproduced).
- **Why left for review:** a new default size limit is behavior-changing and could
  truncate legitimate large payloads. **Recommendation:** add a configurable
  `MqttSettings.max_payload_bytes` (default ~256 KB) and drop oversized messages in
  `_dispatch` before decode; optionally set aiomqtt/paho message limits.

### CICD-02 — Unpinned `pip install maturin` in a contents:write job — **FIXED**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:N/AC:H/PR:H/UI:N/S:U/C:H/I:H/A:H` = 5.9.
- **CWE:** 1104 (Unmaintained/Unpinned Component) / 829.
- **Location:** `.github/workflows/ci.yml` `dependency-submission` job
  (`contents: write` on push to `main`), `run: pip install maturin` — the only step
  installing from PyPI outside `uv.lock`.
- **Attack path:** a compromised maturin (or transitive dep), or a new breaking major,
  executes with a write-scoped token on the `main` branch context (can push
  commits/tags).
- **Confidence:** Confirmed.
- **Remediation (applied):** constrained the install to the same range the build system
  already requires (`pyproject.toml [build-system] requires = ["maturin>=1.12,<2"]`), so
  the job can no longer pull maturin 2.x. **Note:** this bounds the major but not the
  minor/patch. A fully exact/hashed pin maintained by Renovate is the stronger form — I
  could not resolve a verified exact release offline, so it is listed in the hardening
  backlog rather than committed as a guessed version.

### DOCK-03 — Guide offers `privileged: true` as a device-access fix — **LEFT FOR REVIEW**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:L/AC:H/PR:L/UI:N/S:C/C:H/I:H/A:H` = 6.0.
- **CWE:** 250 (Execution with Unnecessary Privileges) / 269.
- **Location:** `docs/guides/deployment.md` (troubleshooting section suggests
  `privileged: true`).
- **Attack path:** a developer hits a `/dev/ttyUSB0` permission error and reaches for
  the offered `privileged: true`; on a Pi with GPIO/i2c/serial passthrough this is a
  trivial container-escape to host root. Safer options (`device_cgroup_rules`, granular
  `devices:`) _are_ listed elsewhere in the guide, which is why this is low.
- **Confidence:** Confirmed.
- **Why left for review:** a documentation-restructure judgment call.
  **Recommendation:** remove `privileged: true`; keep only `device_cgroup_rules` /
  `group_add: [dialout]` with a "never in production" warning. (This is a low-risk doc
  fix and a good candidate for a follow-up commit — deferred here to conserve budget and
  because it touches prose whose surrounding context the maintainer owns.)

### DOCK-02 — Reference compose ships `changeme` plaintext broker password — **LEFT FOR REVIEW**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:A/AC:L/PR:N/UI:N/S:U/C:L/I:L/A:N` = 5.0.
- **CWE:** 798 (Hardcoded Credentials) / 1188.
- **Location:** `docs/guides/deployment.md` (`MYAPP_MQTT__PASSWORD: changeme`);
  Docker-secrets guidance exists but is buried far below.
- **Attack path:** downstream copies `changeme` unchanged; the password in
  `environment:` is exposed via `docker inspect` / `/proc/<pid>/environ`. Reinforces the
  insecure transport default (tls=false/1883/auth None).
- **Confidence:** Confirmed.
- **Why left for review:** candidate for a low-risk doc fix (non-default placeholder + a
  Docker-secrets warning) but deferred to conserve budget; prose ownership sits with the
  maintainer. **Recommendation:** make the primary example use Docker secrets /
  `*_FILE`; no literal default password.

### DEP-01 — Installed venv drifted to mcp 1.27.0 (3 CVEs, unreachable) — **LEFT FOR REVIEW (CI check)**

- **Severity/CVSS:** Low — `CVSS ~4.7`.
- **CWE:** 1104 / 346.
- **Location:** the installed venv shipped `mcp 1.27.0` (transitive via `fastmcp 3.2.4`)
  with CVE-2026-52870 / -52869 / -59950, **but** `uv.lock` pins `mcp 1.28.1` and all
  three CVEs are **UNREACHABLE** — they require WebSocket / multi-session-task
  transports that are never instantiated (this is a **stdio-only** MCP server:
  `_mcp/__main__.py`, `_server.py`).
- **Root cause:** `task security:deps` audits the **lock** (clean); a bare `pip-audit`
  audits the **drifted venv**.
- **Confidence:** Confirmed.
- **Action taken:** I ran `uv sync` to realign the **local** venv to the lock (mcp
  1.28.1). This is environmental and produces **no tracked-file change**, so nothing is
  committed for it.
- **Why left for review:** the committable part is a CI addition (e.g.
  `uv sync --frozen` then run `pip-audit` against the synced env, to catch env-vs-lock
  drift) — a workflow change worth a maintainer decision.

### SEC-01 — Pre-commit is PEM-only; no detect-secrets; `.env` un-ignored — **LEFT FOR REVIEW**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:L/AC:H/PR:L/UI:R/S:U/C:H/I:N/A:N` = 4.2.
- **CWE:** 1188 / 522.
- **Location:** `.pre-commit-config.yaml` (only `detect-private-key`, PEM-only; no
  `detect-secrets`); `.gitignore` (`!**/tests/integration/.env` negates the `.env`
  ignore); CI backstop scans changed files only.
- **Attack path:** `detect-private-key` catches only PEM keys, not `github_pat_` / `sk-`
  / API-key / password formats. A developer creating `packages/tests/integration/.env`
  (un-ignored) with real MQTT creds can `git add .` it and the local gate will not
  block.
- **Confidence:** Confirmed (mechanism); Suspected (no such file exists today).
- **Why left for review (not committed):** the clean fix is to add a `detect-secrets`
  pre-commit hook wired to `.secrets.baseline` **and** refresh the stale baseline
  (SEC-03) in the same change, matching the detect-secrets version used in CI, so
  pre-commit stays green. That is a coupled config change I judged better to land as a
  reviewed unit than to auto-commit under budget pressure. **Recommendation:** add the
  hook + re-baseline + narrow the `.gitignore` negation, together.

### MQTT-03 — Log injection via unvalidated inbound topic — **FIXED**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:L/A:N` = 4.0 (AC:H —
  requires the non-default `LOGGING__FORMAT=text` and control chars in the topic).
- **CWE:** 117 (Improper Output Neutralization for Logs).
- **Location:** raw inbound topic logged at `_mqtt/_router.py` (WARNING ×2),
  `_mqtt/ _client.py` (DEBUG, None-payload skip), `_command_runner.py` (DEBUG, unknown
  sub-topic). Inbound topics are never run through `validate_mqtt_name` (that guards
  registration names only). The default JSON formatter escapes control bytes; the
  **text** formatter does not.
- **Reachability (refined):** the co-tenant-reachable sink is the DEBUG sub-topic path
  (`{prefix}/{device}/<evil>/set`); the WARNING router sinks need a compromised broker
  pushing crafted/unregistered topics.
- **Confidence:** Confirmed (PoC `scratchpad/poc_router_reach.py` — an injected `\n`
  renders as a second log line under the text formatter).
- **Remediation (applied):** render the untrusted topic/device/sub-topic with `%r` in
  the four log calls (repr escapes control bytes on every formatter). No functional
  change; existing substring-based log assertions still pass.

### DOCK-01 — Primary reference Dockerfile/compose unhardened by default — **LEFT FOR REVIEW**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:L/AC:H/PR:L/UI:N/S:U/C:L/I:L/A:L` = 3.9.
- **CWE:** 1188 / 16.
- **Location:** `docs/guides/deployment.md` — primary copy-paste Dockerfile/compose have
  no `read_only` / `cap_drop` / `no-new-privileges` / `mem_limit`; hardened variants
  exist only as an opt-in "consider" section further down.
- **Attack path:** downstream copies the primary artifact; missing resource limits let
  the unbounded-ingress DoS (MQTT-02) OOM the host on a small Pi.
- **Confidence:** Confirmed.
- **Why left for review:** documentation-restructure judgment. **Recommendation:** make
  the _primary_ compose the hardened one, and ship an in-repo `examples/` hardened
  compose + Dockerfile. **(Reconciliation:** recon initially read this as "no hardened
  reference exists" — that was too strong; hardened guidance _is_ present, just not as
  the default artifact.)\*\*

### RUST-02 — MedianFilter window unbounded → alloc abort() — **FIXED**

- **Severity/CVSS:** Low — `CVSS ~3.7`.
- **CWE:** 770 / 789 (Uncontrolled Memory Allocation) / 197 (Numeric Truncation).
- **Location:** `crates/cosalette-filters-rs/src/median.rs` — only `window_val < 1` was
  checked; `VecDeque::with_capacity(w)` eagerly reserves `w*8` bytes;
  `window_val as usize` truncates on 32-bit.
- **Attack path:** if a downstream app wires untrusted data into the `window`
  constructor arg, a huge window (e.g. 1e11) makes the allocation abort — and an
  allocator `abort()` **bypasses pyo3's catch_unwind**, killing the whole daemon (not
  just raising a Python exception). On a 32-bit Pi the value also truncates.
- **Confidence:** Suspected (mechanism confirmed; reachability depends on the app).
- **Remediation (applied):** reject `window > 1_048_576` with `PyValueError` after the
  `< 1` check and before the `as usize` cast. Legitimate median windows are tiny, so the
  cap never rejects real input and keeps the cast exact on 32-bit. Verified with
  `cargo build` + `cargo test` (green). **Note:** the Python `.so` in the tree is built
  by CI via the maturin backend; the source change is what ships — the installed `.so`
  is rebuilt on the next build.

### RUST-03 — OneEuroFilter non-finite internals → permanent NaN — **LEFT FOR REVIEW**

- **Severity/CVSS:** Low — `CVSS ~3.7`.
- **CWE:** 682 (Incorrect Calculation) / 697 (Incorrect Comparison).
- **Location:** `crates/cosalette-filters-rs/src/one_euro.rs` finite-guards the input
  `raw` but **not** the internal `dx_filtered` / `cutoff` / `new_value`; sibling
  `median.rs` even-window average is similarly unguarded.
- **Attack path:** with `beta > 0`, extreme finite floats (±1e308) drive `dx` to ±Inf
  then NaN → `self.value` is permanently NaN until `reset()`.
- **Confidence:** Confirmed mechanism / Suspected impact.
- **Why left for review:** changing extreme-value numeric behavior is an author call —
  clamp vs. reset vs. raise are different product decisions. **Decision needed:** which
  of those three, and whether to apply it to the internal state or only at the boundary.

### MCP-02 — `config_schema` leaks hard-coded secret defaults to LLM — **FIXED**

- **Severity/CVSS:** Low — `CVSS 3.3`.
- **CWE:** 215 (Insertion of Sensitive Info into Debugging Code) / 200.
- **Location:** `_mcp/_config.py` `cosalette_config_schema` returned raw
  `model_json_schema()` with **no** redaction, despite its docstring claiming redaction.
  Redaction helpers (`_is_sensitive`, `_SECRET_NAME_RE`) existed but were used only in
  the env_vars path.
- **Attack path:** a developer's `Settings` source with a hard-coded secret default
  (e.g. `api_key = "sk-..."`) leaks that default verbatim into LLM context via the tool.
  (The tool never instantiates Settings, so a live `.env` is not exposed — this is about
  source-level defaults.)
- **Confidence:** Confirmed (PoC).
- **Remediation (applied):** added `_redact_schema_defaults`, reusing the existing
  `_is_sensitive` helper to replace defaults of secret-looking fields with
  `"<redacted>"` across top-level properties and every `$defs` submodel; applied to a
  `deepcopy` so the shared schema cache is untouched. Regression test added.

### PERS-01 — State files created at process umask — **LEFT FOR REVIEW**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N` = 3.3.
- **CWE:** 732 (Incorrect Permission Assignment).
- **Location:** `_persistence/_stores.py` — `JsonFileStore.save` (`write_text` +
  `os.replace`, no explicit mode) and `SqliteStore` (dir `mkdir` + `connect`, no mode).
  Files inherit the default umask (commonly world-readable).
- **Attack path:** on a shared Pi, a low-priv local user reads
  `~/.local/state/<app>/ store.json` if the app persists sensitive device state.
- **Confidence:** Suspected (code fact confirmed; impact depends on multi-user node +
  app storing sensitive values, which the design discourages).
- **Why left for review:** setting explicit `0o600`/`0o700` modes may surprise operators
  relying on group-readable state — behavior-changing. **Decision needed:** enforce
  restrictive modes by default, vs. document that state may be sensitive and belongs on
  a private-perms directory.

### DOCK-04 — Devcontainer `COPY --from` stages pinned to mutable tags — **LEFT FOR REVIEW**

- **Severity/CVSS:** Low — `CVSS:3.1/AV:N/AC:H/PR:H/UI:R/S:U/C:L/I:L/A:N` = 2.6.
- **CWE:** 1104 / 829.
- **Location:** `.devcontainer/Dockerfile` — the `uv` and `syft` `COPY --from` stages
  are pinned to mutable tags (`uv:0.9.22`, `syft:v1.26.1`); the base image _is_
  digest-pinned.
- **Attack path:** a registry repoint of a mutable tag bakes a malicious binary into the
  image that runs every CI job. Dev/CI only (not fleet); Renovate + manual Dockerfile
  review compensate.
- **Confidence:** Confirmed.
- **Why left for review:** the fix (pin both stages to `@sha256:` digests) is trivial
  _if_ the correct digests can be resolved — which I could **not** do reliably offline
  (no `docker manifest inspect` / `crane` / `skopeo` access, and guessing a digest is
  worse than leaving the tag). **Action needed:** resolve the two digests and pin.

### MCP-03 — `scaffold_adapter` interpolates unvalidated free-text — **FIXED**

- **Severity/CVSS:** Low — `CVSS 2.5`.
- **CWE:** 94.
- **Location:** `_mcp/_scaffolding.py` `cosalette_scaffold_adapter` — only `port_name`
  was validated; `return_type` / `default_value` / `device_description` were
  interpolated verbatim into `adapter.py.j2` with Jinja autoescape off. (Not SSTI — the
  risk is newline/control-char injection of extra source lines into the emitted module.)
- **Confidence:** Confirmed.
- **Remediation (applied, safe part only):** added `_validate_freetext`, which rejects
  control characters / newlines and caps length (200), applied to the three free-text
  fields. Legitimate type annotations, defaults, and descriptions are unaffected.
  Regression tests added for both rejection and legitimate input. Stricter type/value
  validation was intentionally _not_ added (it risks rejecting legitimate expressions
  like `float | None`).

### DEP-02 — `mcp` extra pulls the MCP SDK fully transitively/unpinned — **LEFT FOR REVIEW**

- **Severity:** Info — CWE-1357. Direct deps use unbounded `>=` ranges; the `mcp` extra
  pulls the MCP SDK entirely transitively (`fastmcp>=2.0` already resolved across a
  major to 3.2.4). **Recommendation (dependency policy):** floor `mcp>=1.28.1` in the
  extra and track via Renovate. Left for review — a policy call.

### Assurance / non-findings (verified good posture)

- **IMP-02** — AsyncAPI generator is pure data transformation; no eval/exec/SSTI.
- **RUST-01** — Rust FFI is memory-safe (zero `unsafe`/FFI/raw pointers) and panic-
  contained (`panic=unwind`, pyo3 0.29 `catch_unwind` → `PanicException`, not a daemon
  abort); no MQTT-reachable panic.
- **DEP-03** — lockfiles committed and hash-pinned (uv.lock 768 sha256, all PyPI;
  Cargo.lock all crates.io-checksummed); no git/url/path deps.
- **LEAK-02** — `UnknownSubCommand` reflects only ≤64 bytes of the attacker's _own_
  input, bounded + JSON-escaped, to a topic they can already read. No impact.
- **SEC-02** — the working-tree `/workspace/.env` holds three live-looking dev secrets
  but is correctly gitignored, untracked, and never in history (gitleaks over 772
  commits = clean). Residual = accidental commit via the SEC-01 gap. **Operational
  action:** rotate/ scope those tokens.
- **SEC-03** — `.secrets.baseline` is stale (one orphaned entry for a moved test file).
  Trivial re-baseline; pairs with SEC-01. Left for review with SEC-01 as a unit.

---

## 4. Fixed vs. left-for-review

### Fixed (committed on `security/audit-2026-07-21`)

| Commit    | Finding | Subject                                                                           |
| --------- | ------- | --------------------------------------------------------------------------------- |
| `37bc72e` | MQTT-01 | fix(mqtt): drop non-UTF-8 payloads before dispatch [MQTT-01, CWE-20]              |
| `31cafea` | MQTT-03 | fix(logging): escape untrusted topic in log records [MQTT-03, CWE-117]            |
| `b68b97d` | MCP-02  | fix(mcp): redact sensitive defaults in config schema tool [MCP-02, CWE-215]       |
| `8dfe938` | MCP-03  | fix(mcp): sanitize scaffold free-text inputs [MCP-03, CWE-94]                     |
| `d589f81` | RUST-02 | fix(filters): bound MedianFilter window to prevent alloc abort [RUST-02, CWE-770] |
| `c8035d9` | CICD-02 | fix(ci): constrain maturin in dependency-submission job [CICD-02, CWE-1104]       |

Each fix is minimal and surgical; MQTT-01, MCP-02, and MCP-03 ship regression tests.
`DEP-01` was addressed **locally** by `uv sync` (venv realigned to the lock) — no
tracked-file change, so not committed.

### Left for review (with the decision needed)

- **MCP-01 (High)** — RCE via dynamic import. **Decision:** default-deny import with an
  opt-in env var, vs. a module-prefix allowlist. Risky/behavior-changing. (Doc-alignment
  sub-part can land independently.)
- **LEAK-01 (Med)** — downstream exception strings to broker-wide error topics.
  **Decision:** default to `error_type`-only with an opt-in verbose flag? Removes remote
  diagnostics operators may rely on.
- **MQTT-02 (Med)** — no inbound size cap. **Decision:** default `max_payload_bytes`
  value (proposed ~256 KB)? Could truncate legitimate large payloads.
- **CICD-01 (Med)** — Renovate automerge. **Decision:** `automerge:false` for the
  actions group + `minimumReleaseAge` on deps? Changes maintainer workflow.
- **RUST-03 (Low)** — OneEuro/median non-finite internals. **Decision:** clamp vs. reset
  vs. raise on non-finite internal state?
- **PERS-01 (Low)** — state-file umask. **Decision:** enforce `0o600` by default vs.
  document-only? Behavior-changing for group-readable setups.
- **DOCK-01/02/03 (Low)** — unhardened primary artifacts, `changeme` default cred,
  `privileged: true` troubleshooting. **Decision:** doc restructure — make hardened the
  default, use Docker secrets, remove `privileged`. Low-risk but maintainer owns the
  prose; deferred here under budget. Good candidates for a fast follow-up.
- **DOCK-04 (Low)** — digest-pin the two devcontainer stages. **Action:** resolve the
  sha256 digests (could not offline) and pin.
- **SEC-01 + SEC-03 (Low/Info)** — add a `detect-secrets` pre-commit hook **and**
  refresh the stale baseline together (so pre-commit stays green), and narrow the
  `.gitignore` negation. Land as one reviewed unit.
- **DEP-01 (CI check) / DEP-02 (Info)** — add an env-vs-lock CI check
  (`uv sync --frozen` + `pip-audit`); floor `mcp>=1.28.1` in the `mcp` extra.

---

## 5. Reconciliations (correcting Phase-0 recon)

- **"pip-audit clean"** was **lock-only**. The committed `uv.lock` is clean, but the
  installed venv had drifted to `mcp 1.27.0` with 3 CVEs (DEP-01) — all **unreachable**
  (stdio-only transport). The gap is that `task security:deps` audits the lock while a
  bare `pip-audit` audits the venv.
- **"No hardened deployment reference"** was **too strong**. `docs/guides/deployment.md`
  _does_ contain a non-root multi-stage Dockerfile plus MQTT- and Docker-hardening
  sections — they are just not the **default** copy-paste artifact (DOCK-01).
- **"MCP tools return strings only"** is literally true but **misleading**: 8 of 16
  tools import arbitrary client-specified modules as a side effect (MCP-01), executing
  top-level code before any validation.

---

## 6. Prioritized hardening backlog (left-for-review items)

1. **MCP-01** — gate/allowlist dynamic import in the MCP tools (High; RCE). Also fix the
   docstrings that imply the tools are read-only.
2. **CICD-01** — `automerge:false` for the `github-actions` group; `minimumReleaseAge` +
   strict internal-checks on uv/pre-commit (fleet-wide supply chain).
3. **LEAK-01** — stop publishing raw `str(error)` by default; type-only + opt-in
   verbose.
4. **MQTT-02** — configurable inbound payload size cap; drop oversized before decode.
5. **SEC-01 + SEC-03** — add `detect-secrets` pre-commit hook + refresh baseline +
   narrow the `.env` `.gitignore` negation (one unit).
6. **DEP-01/DEP-02** — env-vs-lock CI check; floor `mcp>=1.28.1` in the extra.
7. **DOCK-01/02/03** — make hardened compose the default, use Docker secrets, drop
   `privileged: true` from troubleshooting.
8. **CICD-02 follow-up** — replace the range with an exact/hashed maturin pin via
   Renovate.
9. **DOCK-04** — digest-pin the two devcontainer `COPY --from` stages.
10. **RUST-03 / PERS-01** — decide non-finite-filter policy; decide state-file mode
    policy.
11. **Operational** — rotate the three dev tokens in the working-tree `.env` (SEC-02).

---

## 7. Limitations / not covered

- **Static + PoC only** — no dynamic/runtime testing, no live MQTT broker, no
  Raspberry-Pi hardware, no fuzzing. Impact claims marked "Suspected" (MQTT-02, RUST-02,
  PERS-01) are reasoned from code, not reproduced end-to-end.
- **PyPI/TestPyPI GitHub _environment_ required-reviewer settings** live in repository
  settings, not in the repo tree, and could **not** be verified from a checkout. The
  release path uses OIDC Trusted Publishing (no static token), which is strong, but the
  human gate on the publish environment is unverified here.
- **hadolint / trivy not run** (not installed in this environment); Dockerfile/image
  scanning was by inspection only.
- **MCP assessed for the stdio transport only** — the WebSocket/multi-session transports
  (which the DEP-01 CVEs require) are never instantiated and were not exercised.
- The Rust `.so` in the working tree was not rebuilt here (maturin not on PATH); the
  RUST-02 source fix is verified via `cargo build`/`cargo test` and is what ships — the
  extension is rebuilt by the maturin build backend in CI/release.
