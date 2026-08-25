# Charter: Full-Stack Security Audit & Hardening — cosalette

Reusable charter for periodic, full-scope security audits of this repository. Feed it to
a security-capable agent (or follow it manually) to plan and execute an audit cycle.
First executed: 2026-08. Re-run cadence: each minor release or after significant
architecture changes.

## Mission

Perform a white-box, state-of-the-art security assessment of the **cosalette** framework
(IoT-to-MQTT bridge framework, Python + PyO3/Rust filter crate, Typer CLI, MCP server,
pydantic-settings, GitHub Actions CI/CD, PyPI distribution) covering:

1. The framework repository itself (source, tests, CI workflows, packaging, docs assets),
2. **Every consumer application built on the framework** — its public API is an attack
   surface and its defaults shape downstream security posture.

Produce: a TARA-grade threat model, a verified findings register, and a prioritized,
deprecation-safe hardening roadmap tracked as beads issues.

## Verified system context (do not re-derive; spot-verify instead)

- Source: `packages/src/cosalette/` (data plane `_mqtt/` `_runners/` `_commands/`; dev
  tooling `_cli.py` `_mcp/` `_schema/` `_settings/` `_persistence/`; support `_logging.py`
  `_health/` `_cron/` `_retry.py`; DI `_injection.py`; test doubles `testing/`).
- Rust crate: `crates/cosalette-filters-rs/` (Pt1/Median/OneEuro, compiled abi3 wheel).
- Existing controls to **verify, not assume**: CodeQL; weekly `task security:audit`
  (`.github/workflows/security.yml`); dependency audits; detect-secrets
  (`.secrets.baseline`); Actions hardening checks; hadolint+Trivy on devcontainer
  (digest-pinned base); SHA-pinned third-party actions; Renovate.
- Known mitigations: inbound MQTT size cap applied **before** UTF-8 decode;
  `validate_mqtt_name` (rejects `+`, `#`, `\0`, C0/DEL) on registration names; pydantic
  `TypeAdapter` command-payload validation; error publishing emits exception **class name
  only** by default (`_errors.py`); MCP import allowlist `COSALETTE_MCP_IMPORT_ALLOW`
  (boundary-aware prefix match, checked *before* `importlib.import_module`; unset ⇒
  refuse); AST-only static introspection tool; `yaml.safe_load` everywhere; `$ref`
  resolution with depth cap; SQLite fully parameterized + WAL + `0o600`/`0o700` perms;
  atomic `mkstemp`+`os.replace` JSON store; `SecretStr` MQTT password; TLS cert/key
  cross-validation; NDJSON logging escapes newlines; Rust crate has **no `unsafe`**, and
  rejects NaN/±Inf.

## Leads & suspected weak spots (verify each; refute or escalate with evidence)

Re-derive this list at the start of every audit cycle; the 2026-08 seeds are:

| ID  | Lead                                                                                     |
| --- | ---------------------------------------------------------------------------------------- |
| L1  | Settings define no `env_prefix` (`extra="ignore"`): generic env vars may silently populate MQTT settings in consumer processes |
| L2  | Escape hatches (`payload: str`, `Payload(raw=True)`) deliver raw broker bytes to handlers — danger documented/loud enough? |
| L3  | `MQTT__ERROR_PUBLISH_VERBOSE=true` publishes raw `str(error)` to broker topics; consumer-supplied `error_type_map` opts messages back in |
| L4  | `_injection.py` uses `eval()` on PEP 563 annotation strings — attacker influence possible in any registration path? |
| L5  | Text-format logging lacks control-char scrubbing; residual log-forging sinks (CWE-117)?   |
| L6  | `one_euro.rs` `unwrap()`s behind a seeded-state invariant; overflow/allocation-bomb paths (median window size)? |
| L7  | `ai init --target` writes arbitrary user-supplied path; merges JSONC into agent configs — symlink/overwrite/injection behavior? |
| L8  | Schema loader: YAML alias bombs via `safe_load`; `$ref` depth cap adequacy                |
| L9  | MCP tool outputs feed agent context — prompt-injection/stored-content surface; package-data integrity of `assets/guidance/*` |
| L10 | Retained-entity cleanup trusts a snapshot file — trust/placement/replay impact            |
| L11 | Test harness `inject_command()` bypasses `validate_mqtt_name` — enforce test-only boundary|
| L12 | Committed artifacts (`coverage.*`, `results-*.xml`, `test-report.html`, `site/`, root `issues.jsonl`) information-disclosure sweep |
| L13 | Cron parser / retry jitter assumed developer-input-only — confirm no remote path          |
| L14 | Sub-command truncation to 64 chars in error topics — partial-leak edges                   |

## Standards & baselines

- **TARA**: ISO/SAE 21434-inspired method adapted to a software framework: asset
  identification → threat scenarios (**STRIDE per element**) → attack trees for top risks →
  5×5 risk matrix → treatment & residual risk. Map every scenario to CWE IDs.
- **OWASP**: Top 10 (latest ratified edition), **ASVS** (state version used) level 2 gap
  matrix, IoT Top 10 (device/bridge lens), WSTG only where web-adjacent.
- **LLM/Agentic/MCP**: OWASP Top 10 for LLM Applications (2025), OWASP Agentic-AI threat
  taxonomy, official MCP specification security best practices (confused deputy, tool
  poisoning/"rug pull", token passthrough, prompt injection, session/transport rules).
- **Supply chain & CI/CD**: SLSA v1.x (current level + next target), NIST SP 800-218 SSDF,
  OpenSSF Scorecard (full local run), CIS Software Supply Chain Security Guide, GitHub
  Actions review (pwn requests, untrusted `${{ }}` interpolation, cache/artifact poisoning,
  OIDC least privilege) via `zizmor --strict` + manual review, PyPI **Trusted
  Publishing/attestations**, SBOM (CycloneDX: Python + Rust + container layers),
  `cargo audit`/`cargo deny`/`cargo vet`, lockfile hygiene, Renovate/digest pinning,
  detect-secrets baseline freshness.
- **IoT regulatory readiness (advisory)**: EU Cyber Resilience Act obligations relevant to
  an IoT framework distributor; ETSI EN 303 645 provisions applicable to consumers' devices.

## Phases

- **P0 Scope & architecture**: entry-point inventory; data-flow diagram (mermaid) with
  trust boundaries: broker⇄app, app⇄hardware adapters, developer-tool⇄repository, MCP⇄agent,
  CI⇄GitHub, release⇄PyPI, **framework⇄consumer app**.
- **P1 TARA**: scenarios per boundary; attack trees ≥ top-3 risks (candidates: forged
  command/state injection via compromised publisher; RCE via `module:app` import paths;
  supply-chain implant reaching published wheels); scored risk register.
- **P2 Data-plane review**: full inbound MQTT pipeline ordering (cap→decode→route→parse),
  JSON sub-dispatch (nesting/size DoS), typed-contract validation limits, handler timeout
  coverage, error-topic leak valves, retained/LWT/QoS-replay semantics, name-validation gaps.
- **P3 Developer/tooling-plane review**: CLI manifest import boundary; MCP allowlist bypass
  attempts (prefix confusion, unicode confusables, symlink/TOCTOU between check and import);
  scaffolder output-injection into generated code; `ai init` path/config-merge behavior;
  schema loader robustness; settings env-namespace collision (L1); persistence stores;
  logging sinks (L5).
- **P4 MCP/LLM-specific review** (extends P3): tools vs OWASP LLM/Agentic + MCP spec;
  guidance-asset integrity; static AST analyzer DoS; document residual prompt-injection
  exposure honestly rather than pretending it away.
- **P5 Rust FFI review**: panics-across-FFI audit (L6), arithmetic overflow in release
  profile, allocation amplification, pyo3/abi3 currency, wheel-build workflow trust.
- **P6 Supply-chain & CI/CD review**: per standards above; artifact-disclosure sweep (L12);
  release-process compromise-path walk-through (release-please → PyPI).
- **P7 Consumer-protection review**: secure-by-default audit of every public knob; footgun
  inventory with mitigation/deprecation plan (semver-aware); consumer threat-model +
  deployment-hardening guide completeness (authn, ACLs, mTLS, TLS CA, network segmentation,
  retained-message data visibility); SECURITY.md accuracy pass.
- **P8 Adversarial validation**: property-based adversarial strategies (Hypothesis) for
  topic strings, JSON dispatch, schema refs, cron fields; timeboxed fuzzer spike on pure
  parsers; PoC suite under `packages/tests/security/` reproducing/refuting every confirmed
  high-severity lead — in-process harness or throwaway local broker only.
- **P9 Reporting & remediation**: consolidated report; ASVS gap matrix; risk register with
  treatments; hardening roadmap split into quick wins vs structural changes (ADR-worthy);
  proposed new CI gates wired into `Taskfile.yml` + `security.yml`; doc updates
  (`SECURITY.md`, threat-model page). Close out by rerunning `task security:audit` and the
  test suite green.

## Rules of engagement

- White-box. Execution mode is **audit + full hardening**: confirmed findings are fixed
  directly (regression-test-first), including structural changes where feasible; changes
  stay semver-aware — anything breaking consumer behavior lands behind deprecation
  warnings or an ADR per repo policy. Never weaken existing gates to make scans pass.
- Evidence discipline: every finding cites `file:line`, includes impact, exploit
  preconditions, CVSS v4.0 vector, CWE, and a minimal PoC or reasoned refutation. Classify
  as [VULNERABILITY | HARDENING | DOC-GAP]. No speculative severity inflation.
- Sandbox only: PoCs run in-process or against ephemeral local brokers/containers. No
  external scanning, no production systems, no real credentials. If a genuine secret is
  found: report location only, never echo the value; recommend rotation.
- Time-box rabbit holes; park them as explicit leads in the risk register.
- All work tracked in beads per the breakdown below; conservative git policy (no push
  without authority).

## Deliverables

1. `docs/security/threat-model.md` — DFD, assets, boundaries, STRIDE tables, attack trees,
   risk matrix.
2. Findings register — one beads issue per confirmed finding (severity, CVSS, CWE, PoC,
   fix recommendation, affected consumers).
3. ASVS gap matrix + supply-chain checklist (SLSA/CIS/Scorecard) under `docs/security/`.
4. Prioritized hardening roadmap (quick wins vs structural/ADR) + consumer-migration notes.
5. Regression tests for every fixed finding; proposed CI additions; updated SECURITY.md.

## Work breakdown for beads planning

Epic: "Security audit & hardening (TARA/OWASP/supply-chain)" — P1. Children (deps):

1. P0 Scope, DFD, trust-boundary & asset inventory — P1
2. P1 TARA: STRIDE, attack trees, risk register (dep 1) — P1
3. P2 Data-plane security review (dep 1) — P1
4. P3 Tooling-plane review: CLI/MCP-import/schema/settings/persistence/logging (dep 1) — P1
5. P4 MCP/LLM-agentic review (dep 4) — P2
6. P5 Rust FFI crate review (dep 1) — P2
7. P6 Supply chain & CI/CD review (dep 1) — P1
8. P7 Consumer secure-defaults & misuse-resistance review (dep 1) — P1
9. P8 Adversarial validation: fuzzing + PoC suite (dep 2,3,4,5) — P2
10. P9 Consolidated report, matrices, hardening roadmap + fixes (dep 7,8,9) — P1
11. Gate task: deferred low-severity findings backlog (dep 10) — P3

Each child carries explicit acceptance criteria from the corresponding phase above.

## 2026-08 execution notes

- Mode: audit + full hardening.
- Findings filed as beads issues off epic; fixes reference their finding ID in commits.
