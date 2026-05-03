# Security Audit Evidence Matrix

Date: 2026-05-03
Status: Implementation in progress
Epic: cos-py2

## Threat Model

### Assets

- MQTT credentials and broker connection details.
- MQTT command topics, state topics, availability topics, and retained metadata.
- Device state persisted through JSON or SQLite stores.
- Schema documents and generated broker ACL configuration.
- MCP tool outputs, app introspection manifests, and generated scaffolding.
- Developer workstation files touched by CLI initialization commands.
- GitHub Actions tokens, repository secrets, release credentials, artifacts, SBOMs,
  and PyPI/TestPyPI publication integrity.
- Published package wheels and source distributions.

### Trust Boundaries

- User application code registered with the framework.
- MQTT broker and network peers.
- Local filesystem paths supplied through settings and CLI options.
- AsyncAPI schema files loaded from disk.
- MCP clients invoking local tools.
- GitHub pull request inputs, workflow expressions, and release automation.
- Third-party Python, Rust, Node, container, and GitHub Action dependencies.

### Attacker Profiles

- A broker peer that can publish malformed or unauthorized MQTT messages.
- A user or process that can provide hostile schema/configuration files.
- A malicious or compromised local workspace invoking MCP/CLI tooling.
- An untrusted pull request author attempting workflow injection or artifact abuse.
- A compromised third-party dependency or GitHub Action.
- A package supply-chain attacker attempting to alter release artifacts or metadata.

## Baseline Results

| Check | Command | Result | Evidence |
| ----- | ------- | ------ | -------- |
| Lint | `task lint` | Passed | Ruff check and format check clean. |
| Typecheck | `task typecheck` | Passed | `ty check` clean. |
| Unit tests | `task test:unit` | Passed | 2150 tests passed. |
| Integration tests | `task test:integration` | Passed | 57 passed, 10 deselected. |
| Python dependency audit | `task security:deps` | Passed | Locked dependency export clean. |
| Rust dependency audit | `task security:rust` | Passed | `cargo audit --file Cargo.lock` clean. |
| Python SAST gate | `task security:python` | Passed | Production package security lint clean with scoped ignores for assertions and jitter. |
| Secret scan gate | `task security:secrets` | Passed | Baseline committed for known false positives. |
| GitHub Actions lint/security | `task security:actions` | Passed | actionlint plus high-confidence zizmor findings clean. |
| Aggregate security audit | `task security:audit` | Passed | Dependency, secret, Python SAST, Rust audit, and Actions checks pass. |

## Control Matrix

| Control | Surface | Evidence | Result | Finding |
| ------- | ------- | -------- | ------ | ------- |
| Dependency vulnerability scanning | Python dependencies | `task security:deps` | Pass | None |
| Dependency vulnerability scanning | Rust dependencies | `task security:rust` | Pass | None |
| Secret scanning | Repository contents | `task security:secrets` | Pass | cos-sz5 |
| Python security linting | Production package code | `task security:python` | Pass | cos-sz5 |
| Workflow shell injection prevention | GitHub Actions | actionlint and zizmor | Pass | cos-4ze |
| Immutable action references | GitHub Actions supply chain | pinned action SHAs plus zizmor | Pass | cos-g39 |
| Least privilege workflow tokens | GitHub Actions permissions | scoped permissions and checkout credentials | Pass | cos-53h |
| Scheduled security scans | CI | `.github/workflows/security.yml` | Pass | cos-sz5 |
| Pre-PR security scans | Local task gate | `task pre-pr` includes `security:audit` | Pass | cos-sz5 |
| MQTT network hardening guidance | User docs and `SECURITY.md` | TLS/auth/ACL guidance plus first-class client TLS settings | Pass | cos-h5w |
| MCP remote transport posture | CLI/MCP implementation | SSE rejected; stdio-only docs and tests | Pass | cos-df0 |

## Confirmed Findings

### cos-4ze: GitHub Actions Shell Template Injection

`actionlint` and `zizmor` flagged direct GitHub expression interpolation inside shell
`run` blocks. The clearest instance is `.github/workflows/ci.yml`, where
`github.head_ref` is expanded into a Bash conditional. GitHub treats PR metadata as
untrusted input; passing values through `env` avoids code-shaped interpolation before
the shell parses the script.

### cos-g39: Unpinned Third-Party GitHub Actions

`zizmor` found tag-only action references across CI, docs, integration, release, and
Rust wheel workflows. The approved policy requires immutable SHA pinning for all
third-party actions, with version comments kept for maintainability.

### cos-53h: Checkout Credential Persistence And Broad Permissions

`zizmor` found checkout steps that keep credentials in workspaces and workflow-level
permissions broader than necessary. Most checkouts should set `persist-credentials:
false`; push jobs need explicit exceptions.

### cos-sz5: Missing Repeatable Security Scan Gates

Security tools are currently ad hoc. `task pre-pr` and CI need repeatable security
tasks for dependency, secret, SAST, and workflow checks.

### cos-df0: MCP SSE Transport Exposes Dynamic Imports

MCP introspection accepts `app_spec` and `settings_spec` values that dynamically
import local Python modules. This is acceptable over local stdio because the IDE
caller already runs code in the workspace. SSE would make the same imports
network-reachable, so the CLI now rejects non-stdio transport and the MCP guide
documents stdio as the only supported transport.

### cos-h5w: MQTT TLS/Auth Guidance Was Not Actionable Enough

Broker-level TLS and authentication guidance is the right layer for deployment,
but cosalette also needed a way to pass TLS settings to the MQTT client. The base
settings now expose `mqtt.tls`, CA bundle, and mutual-TLS cert/key fields; the
client builds a verifying `ssl.SSLContext` when TLS is enabled.

## Triage Notes

- Ruff `S311` on reconnect/retry jitter is a false positive: jitter is load spreading,
  not a cryptographic token or security boundary.
- Ruff `S101` assertions are mostly invariant checks and pytest tests. The production
  assertions can be reviewed separately, but they are not currently confirmed
  exploitable findings.
- The Docker GPG fingerprint and `SecretStr("s3cret")` test fixture are secret-scan
  false positives that should be captured in a baseline rather than removed.
- The docs preview teardown uses `pull_request_target` only for PR-close cleanup. It
  does not check out code or execute PR-controlled files; the residual zizmor finding
  is tracked as a documented low-risk exception rather than a blocking gate.
- `zizmor --min-severity high --min-confidence high` is used as the blocking gate;
  lower-confidence findings stay documented for manual review rather than blocking
  unrelated PRs.
