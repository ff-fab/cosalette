# Security Audit Report — 2026-08

**Charter:** [audit-charter.md](audit-charter.md) · **Threat model:**
[threat-model.md](threat-model.md) · **Beads epic:** `cos-juyi`
**Mode:** audit + full hardening · **Standards:** OWASP Top 10 / ASVS L2 lens,
OWASP LLM & Agentic taxonomies, MCP spec security practices, SLSA/CIS-SSC/SSDF
checklist, ISO 21434-inspired TARA.

## Verdict

No remotely exploitable vulnerability was found in the framework's MQTT data plane.
The inbound pipeline (size cap before decode → UTF-8 gate → exact/longest-prefix
routing → typed-contract validation → default-deny error redaction) and the MCP import
allowlist survived adversarial review. The confirmed issues are concentrated in the
developer-tooling plane (scaffold input validation, env-name collision ergonomics),
resource-exhaustion edge paths, and consumer-facing secure-default guidance — all
remediated or explicitly scheduled below.

## Findings register

| ID | Title | Class | Severity | CWE | Status |
| --- | --- | --- | --- | --- | --- |
| F-TP8 | Scaffold `dry_run_name` injected into generated test module | VULNERABILITY | Medium | 94 | **Fixed** (`_mcp/_scaffolding.py`, regression test) |
| F-TP1 | Env namespace squatting on reserved `MQTT`/`LOGGING`/`SCHEMA` vars; opaque startup crash | VULNERABILITY | Medium | 15 | **Fixed** (actionable error translation + reserved-name docs; default-prefix change deferred to the next 0.x minor release, 0.7.0) |
| F-DP5 | Unbounded command timeout stalls FIFO worker; periodic/device sub-handlers lack timeouts | HARDENING | Medium | 400 | **Fixed** (ADR-060: 30 s command default, periodic interval-watchdog, device-context bound; opt-out `timeout=None`) |
| F-SC1 | Personal e-mails committed in tracked `issues.jsonl` export | VULNERABILITY (privacy) | Medium | 359 | **Fixed** (untracked + ignored); history rewrite gated on maintainer authority (`cos-juyi.22`) |
| F-DP1 | `error_type_map` conflates labeling with message disclosure | HARDENING | Med-Low | 209/532 | **Fixed** (ADR-061: opt-in `disclose_messages_for` set decouples message disclosure from labeling; `None` preserves legacy behaviour, default flip targeted for 0.7.0) |
| F-SC2 | Live tokens in local `.env` invisible to staged-file scanners | Operational | High (local) | 798 | User action required: rotate all three credentials |
| F-DP4 | Deep-nested JSON raises unstructured RecursionError instead of `invalid_json` | HARDENING | Low | 674 | **Fixed** (+ adversarial regression test) |
| F-DP9 | F-DP4 residual: stdlib `json.loads` outside the `_json` choke-point (`TriggerPayload.from_mqtt`, monitor `_dispatch_message`) still raised uncaught `RecursionError`; monitor also `AttributeError`'d on non-object schema/status payloads | HARDENING | Low | 674/755 | **Fixed** (routed through orjson choke-point + object guard; found by the new atheris harnesses, regression tests added) |
| F-DP2 | Sub-command echo publishes ≤64 attacker-chosen chars to error topics | HARDENING | Low | 209 | **Fixed** (fingerprint echo; raw value local-log only) |
| F-TP3 | Text-mode log forging via LF in schema-monitor topics | HARDENING | Low | 117 | **Fixed** (`%r` quoting) |
| F-TP7 | Static-describe unbounded read/parse of agent-chosen file | HARDENING | Low | 400/674 | **Fixed** (2 MB cap + complexity guard) |
| F-TP9 | Harness `inject_command` bypasses production name validation | HARDENING | Low | 20 | **Fixed** (validate by default, `unsafe=True` opt-out) |
| F-CU1 | Plaintext credentials when `tls=false` on non-local broker | HARDENING | Medium | 1188/319 | **Mitigated** (startup warning); default flip planned for 0.7.0 |
| F-CU2 | Anonymous join of non-local brokers by default | HARDENING | Low-Med | 1188 | **Mitigated** (startup warning) |
| F-SC4 | Fork-PR cache-save surface in devcontainer action | HARDENING | Low | 406 | **Fixed** (`event_name != pull_request`) |
| F-SC6 | No cargo-deny license/advisory/ban gate for Rust crate | HARDENING | Low | – | **Added** (`deny.toml` + guarded Taskfile cmd) |
| F-SC5 | OpenSSF Scorecard workflow absent (badges pending: cos-4kv/cos-e7u) | DOC-GAP | Low-Med | – | **Added** (weekly scorecard.yml) |
| F-SC7 | SECURITY.md overstates Dependabot coverage (Renovate is engine) | DOC-GAP | Low | – | **Fixed** |
| F-CU7 | Deployment docs lack update/re-scan story | DOC-GAP | Low | – | **Fixed** ("Updating the Deployment") |
| F-DP7 | Retained-topic spoofability without broker ACLs undocumented | DOC-GAP | Low | 20 | **Fixed** (ACL subsection + example) |
| F-DP8 | Raw-payload escape hatches lack security wording | DOC-GAP | Low | 20 | **Fixed** (Payload/Message warnings) |
| F-DP3 | Retained-cleanup snapshot: single-writer assumption undocumented | DOC-GAP | Low | 345/367 | Documented (fail-closed chain verified; HMAC-signing optional future) |
| F-DP6 | Heartbeat discloses app version (CVE fingerprinting) | DOC-GAP | Info | 200 | Deferred (opt-out knob candidate) |
| F-SC3 | `pull_request_target` in docs.yml teardown | Verified safe | – | 829 | Accepted (closed-type trigger, no checkout, numeric interpolation only) |
| F-L6 | `one_euro.rs` invariant-backed `unwrap()`s | Verified safe | – | – | Refuted (seed/reset invariant sound; PyO3 converts panics; no `panic=abort`) |

Refuted leads (evidence in threat model §6): eval-based annotation injection (F-TP2),
MCP allowlist bypass via confusables/case/symlink (F-TP6), retained-cleanup live-topic
wipe (F-DP3), topic-prefix collision routing, JSONC merge injection, scaffold content
injection via other template inputs.

## ASVS L2 gap matrix (selected)

| Area | Status |
| --- | --- |
| V2 Authentication | Broker authn is deployment-side; framework warns on anonymous/plaintext posture ✓ |
| V3 Session management | n/a (MQTT session = broker); reconnect/LWT semantics reviewed ✓ |
| V4 Access control | Topic-level ACLs documented as mandatory (retained-trust subsection) ✓ |
| V5 Validation | Typed contracts + size-cap-before-decode + name validation; recursion edge closed ✓ |
| V7 Errors & logging | Default-deny error redaction; log-forging sinks closed; verbose valve documented ✓ |
| V8 Data protection | SecretStr, owner-only files, atomic writes; TLS validators fail fast ✓ |
| V14 Config | Reserved-env-name collision surfaced actionably; prefix guidance ✓ |
| V1.11/1.14 (business logic/infra as code) | Workflows least-privilege, SHA-pinned, zizmor/actionlint clean ✓ |

## Supply-chain checklist

SLSA build L3 (isolated, non-falsifiable provenance via Trusted Publishing +
attestations + SBOM). CIS-SSC spot-checks pass. Scorecard workflow added — expect
initial gaps on "Fuzzing" and "Dependency Tool" (Renovate counts). Note: the
atheris/libFuzzer harnesses (roadmap #6) fuzz the parsers in-repo; the Scorecard
"Fuzzing" check specifically requires OSS-Fuzz (or ClusterFuzz Lite) integration,
which remains open as a follow-up option.

## Hardening roadmap

**Quick wins (done this cycle):** see Fixed rows above.

**Structural (ADR-worthy, scheduled):**
1. ~~Bounded default command timeout + periodic-loop watchdog (F-DP5)~~ — **done (ADR-060)**.
2. ~~Decouple `error_type_map` labeling from message release (F-DP1)~~ — **done (ADR-061)**.
3. Default-on TLS / required explicit opt-out at 0.7.0 (F-CU1) — minor bump.
4. Optional heartbeat version omission flag (F-DP6).
5. HMAC-signed cleanup snapshots (F-DP3 hardening option).
6. ~~Fuzzing CI job (atheris/libFuzzer harness on pure parsers) — closes Scorecard gap~~ — **done** (`task security:fuzz`, weekly Security workflow job; first campaign found and fixed F-DP9; in-repo fuzzing does not satisfy the Scorecard "Fuzzing" check — that requires OSS-Fuzz/ClusterFuzz Lite, optional follow-up).
7. History rewrite to purge PII from `issues.jsonl` (needs maintainer authority,
   `cos-juyi.22`).

**User actions:** rotate the three live tokens found in local `.env` (F-SC2).
