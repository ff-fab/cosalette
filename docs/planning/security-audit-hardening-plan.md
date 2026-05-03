# Security Audit And Hardening Plan

Date: 2026-05-03
Status: Approved for implementation

## Goal

Perform a full security audit of cosalette, then fix all confirmed findings in a
controlled hardening pass. The audit should cover the Python framework, Rust/PyO3
extension, MQTT behavior, schema and ACL generation, persistence, MCP and CLI
surfaces, GitHub Actions, release automation, dependency supply chain, and
documentation that affects secure use.

The desired output is not only a list of issues. The work should leave the project
with repeatable security checks, tests for fixed vulnerabilities, and clear
deployment guidance for users building IoT-to-MQTT bridges with cosalette.

## Current Baseline From Reconnaissance

Existing strengths:

- CodeQL runs on PRs and pushes through CI, with a scheduled weekly scan.
- Renovate is configured for dependency freshness and vulnerability alerts.
- Release automation already uses OIDC publishing, build provenance attestations,
  TestPyPI before PyPI, SBOM generation, and protected environments.
- Several write paths in the package CLI already use symlink checks plus atomic
  `os.replace()` writes for CWE-59-style hardening.
- Schema loading uses `yaml.safe_load()` and internal `$ref` resolution has a
  recursion depth cap.
- Rust filter inputs reject bools and non-finite floats at the PyO3 boundary.

Important review targets:

- MQTT settings currently model host, port, credentials, client id, reconnects,
  and topic prefix; TLS posture and secure defaults need explicit review against
  `SECURITY.md` guidance.
- MQTT topic routing, schema-derived ACL generation, and retained `_meta/registry`
  publication are authorization and information-disclosure surfaces.
- MCP tools dynamically import user-provided `module:attribute` specs; this is
  documented as local-only but needs review for SSE usage and secure defaults.
- CLI/config helpers write project files and modify agent/tool configuration.
- `JsonFileStore`, SQLite persistence, and log-file paths rely on configured file
  paths and should be reviewed for symlink, path traversal, file permission, and
  crash-consistency issues.
- Dependency and secret scanning are partially covered by pre-commit
  `detect-private-key`, Renovate, and CodeQL, but there is no obvious first-class
  `pip-audit`, `cargo audit`, Semgrep, Gitleaks, or TruffleHog task yet.
- Several GitHub Actions are pinned by SHA, but some use version tags. This should
  become an explicit policy rather than an incidental mixture.

These are hypotheses and audit targets, not final findings. Each must be verified
with evidence before becoming a fix task.

## How To Use A Strong AI Reviewer Effectively

Use the AI as an adversarial reviewer inside a deterministic audit loop, not as a
replacement for tools. The strongest pattern is:

1. Give it one bounded surface at a time, such as MQTT routing, MCP imports, or a
   workflow file.
2. Provide the local threat model, relevant files, scan output, and existing ADRs.
3. Ask for findings with exploit scenario, affected asset, severity, CWE or OWASP
   category when applicable, proof or test idea, and minimal fix.
4. Verify each claim manually and with tests or scanners before changing code.
5. Ask a second pass after fixes: "How would you bypass this mitigation?"

Do not paste real secrets, tokens, private production broker details, or customer
data into any external AI tool. If a model needs context, provide code, redacted
configuration shapes, local scan output, and synthetic examples.

## Options

### Option A: Tool-Assisted Full Audit, Then Severity-Sliced Fixes

**What it does:** Run a whole-project audit using deterministic scanners, manual
threat modeling, and AI reviewer passes. Confirm findings, file them as beads tasks,
then fix them in severity order across one or more PRs.

**Implementation:**

```text
security-audit epic
  phase 1: baseline scans and threat model
  phase 2: source review by surface
  phase 3: CI, release, and dependency supply-chain review
  phase 4: findings triage and fix plan
  phase 5: implement fixes with tests
  phase 6: hardening gates and documentation
```

**Why this approach:**

- It follows defense in depth: static analysis, dependency intelligence, manual
  review, runtime tests, and documentation all catch different classes of issues.
- It keeps the Single Responsibility Principle at the process level: audit,
  triage, fixing, and prevention are separate steps with different outputs.
- It reduces false positives because every AI or scanner finding must be
  confirmed before it becomes a code change.
- It is compatible with this repo's workflow: beads tasks, `task` wrappers,
  pre-PR gates, CodeQL, release automation, and focused PR review.

**Trade-offs:**

- Slower than a single opportunistic scan.
- Requires discipline to avoid expanding into unrelated refactors.
- May produce several PRs if findings span unrelated ownership areas.

**Tooling note:** Prefer adding repeatable `task security:*` wrappers for any scan
that proves valuable, so future agents and maintainers do not need to memorize raw
scanner commands.

### Option B: Fast Hardening Sweep

**What it does:** Run a smaller scanner set, review obvious sensitive surfaces, and
fix only high-confidence issues found during that pass.

**Implementation:** Run CodeQL, dependency audits, secret scans, and targeted review
of MQTT, MCP, file writes, and workflows. Skip broad test expansion unless a finding
needs it.

**Why this approach:**

- Produces useful hardening quickly.
- Good when release pressure is high and the goal is risk reduction, not audit
  completeness.
- Lower process overhead.

**Trade-offs:**

- Easier to miss design-level issues, insecure defaults, and docs-to-code mismatch.
- Does not leave as strong a repeatable audit trail.
- "No findings" from a short pass is not strong evidence of security posture.

**Tooling note:** This is best as an interim pass, not the final answer to a full
project audit request.

### Option C: External-Standard Audit Package

**What it does:** Shape the audit around a formal checklist such as OWASP ASVS
concepts adapted to a library/IoT framework, OWASP Top 10 where relevant, SLSA for
supply chain, and GitHub Actions hardening guidance.

**Implementation:** Create a formal matrix of controls, evidence, result, severity,
owner, and fix PR. Use the matrix as the source of truth for sign-off.

**Why this approach:**

- Excellent evidence trail for future maintainers and users.
- Helps communicate security posture publicly.
- Strong for supply-chain and release-process assurance.

**Trade-offs:**

- More documentation and audit-administration overhead.
- Some web-app controls do not map cleanly to a Python IoT framework.
- May slow code fixes if the matrix becomes the focus.

**Tooling note:** This is useful if the project wants a public security posture page
or a recurring quarterly security review process.

## Recommendation

Use Option A, with a light control matrix borrowed from Option C for evidence. It
best matches the request for a full audit followed by fixes, while keeping the work
practical and aligned with the repository's existing task-driven workflow.

## Audit Scope

### Application And Library Code

- MQTT client connection lifecycle, credentials, TLS support, topic validation,
  subscription restoration, QoS choices, retained messages, and reconnect behavior.
- TopicRouter parsing and command dispatch, especially wildcard behavior,
  prefix/device validation, root devices, sub-topic dispatch, and failure modes.
- Schema loader, validator, monitor, consumer generation, ACL derivation, and CLI
  commands. Review YAML parsing, `$ref` expansion, JSON schema validation, topic
  wildcard handling, and generated broker configs.
- Persistence stores, including JSON file atomicity, SQLite setup, file modes,
  symlink/path risks, corrupt file behavior, and state isolation.
- Logging and error publishing, including secret redaction, stack trace exposure,
  structured log fields, retained error topics, and file sinks.
- Dependency injection and dynamic annotation resolution, including the documented
  `eval()` fallback and whether safer alternatives can preserve developer ergonomics.
- MCP server, dynamic imports, SSE transport, scaffolding template rendering,
  introspection output, secret redaction, and local-only assumptions.
- Package CLI commands that write files or inspect app modules.
- Rust/PyO3 filters for panics, unwrap invariants, input validation, memory safety,
  denial-of-service edge cases, and Python/Rust behavior parity.

### Tests And Tooling

- Unit and integration tests covering security fixes.
- Property-based tests for parser-like logic: MQTT topics, schema `$ref`s, JSONC
  comment stripping, cron/schedule parsing, and Rust filter input boundaries.
- MQTT integration tests for authentication, TLS if added, ACL-compatible topics,
  retained metadata, and command routing failure cases.
- Template smoke tests for generated code after MCP or CLI hardening.

### Supply Chain And CI/CD

- Python dependency vulnerabilities from `uv.lock`.
- Rust dependency vulnerabilities from `Cargo.lock`.
- GitHub Actions permissions, pinning policy, `pull_request_target` safety, secrets
  exposure, artifact handling, and release environment protections.
- Devcontainer Docker-in-Docker and privileged mode assumptions.
- SBOM generation and dependency submission coverage.
- Release Please, PyPI/TestPyPI, attestations, draft release mutation window, and
  post-release `SECURITY.md` update behavior.

### Documentation And Secure Defaults

- `SECURITY.md`, deployment docs, configuration docs, MCP server docs, and MQTT
  broker guidance.
- Ensure docs do not encourage insecure examples such as plaintext broker auth in
  production without a clear warning.
- Align documentation with implemented defaults.

## Scanner And Review Toolkit

Use existing tasks first. New recurring commands should become `Taskfile.yml`
wrappers before becoming part of the quality gate.

Baseline checks already present:

- `task lint`
- `task typecheck`
- `task test:unit`
- `task test:integration`
- `task template:check`
- `task pre-pr`
- CodeQL in CI and scheduled workflow

Candidate audit tools to evaluate and wrap:

- `pip-audit` for Python dependency CVEs from the locked environment.
- `cargo audit` or `cargo deny` for Rust dependency advisories and policy checks.
- `gitleaks` or `trufflehog` for repository secret scanning.
- Ruff `S` rules or Bandit for Python security linting. Ruff is preferable if the
  rule coverage is sufficient because it fits the existing toolchain.
- Semgrep for language-agnostic security patterns, especially GitHub Actions and
  Python file/path handling.
- `actionlint` for GitHub Actions correctness and shell-in-workflow hazards.
- Scorecard or zizmor for GitHub Actions and supply-chain posture, if noise is
  manageable.

Potential tasks to add after evaluation:

```yaml
security:deps      # Python and Rust dependency advisories
security:secrets   # repository secret scan
security:sast      # CodeQL-compatible local checks, Semgrep/Ruff S if adopted
security:actions   # workflow hardening checks
security:audit     # aggregate local security audit gate
```

## Triage Rules

Severity should be based on exploitability, impact, and whether the affected surface
is reachable in normal cosalette use.

- Critical: credential exposure, remote code execution, publish bypass, release
  compromise path, or unauthenticated remote control in default configuration.
- High: path traversal/file clobbering from realistic input, MQTT authorization gap,
  unsafe CI secret exposure, or insecure default that affects production users.
- Medium: denial of service, information disclosure, weak hardening around local-only
  tooling, missing validation with constrained reachability, or dependency CVEs with
  limited exploitability.
- Low: defense-in-depth improvements, documentation gaps, noisy scanner findings, or
  hardening that reduces future risk without closing an immediate exploit.

Each confirmed finding should include:

- Affected file or workflow.
- Threat actor and preconditions.
- Exploit sketch or failure mode.
- Severity and rationale.
- Minimal fix.
- Regression test or verification command.
- Whether it blocks release.

False positives should be documented briefly so the same scanner result does not get
re-litigated in later passes.

## Execution Plan

### Phase 0: Setup And Work Tracking

- Create a security audit epic in beads after this plan is approved.
- Create child tasks for each audit surface and for any confirmed finding.
- Confirm branch strategy before code changes. Do not work directly on `main` for
  fixes.
- Record baseline git status so unrelated local changes remain untouched.

### Phase 1: Threat Model

- Identify assets: MQTT credentials, broker topics, device commands, retained state,
  schema registry metadata, generated config files, release credentials, PyPI package
  integrity, docs deployment secrets, and developer machines using MCP tools.
- Identify trust boundaries: user app code, broker network, filesystem, schema files,
  MCP clients, GitHub Actions, package consumers, and generated scaffolding.
- List attacker profiles: malicious broker peer, compromised schema/config file,
  malicious local workspace, untrusted PR author, compromised dependency, and package
  supply-chain attacker.

### Phase 2: Deterministic Scans

- Run current quality gates to know whether the repo is clean before security work.
- Run dependency, secret, SAST, GitHub Actions, and Rust checks.
- Save scanner outputs in a temporary workspace artifact or summarize them in the
  audit report; do not commit bulky raw reports unless they add lasting value.
- Convert confirmed results into beads findings.

### Phase 3: Manual And AI-Assisted Source Review

- Review one surface at a time using the audit scope above.
- Use the existing security reviewer agent pattern for adversarial checks.
- For each surface, ask the AI for bypasses, not just obvious bugs.
- Confirm claims against source code, tests, and docs before accepting a finding.

### Phase 4: Fix Planning

- Group findings by shared root cause and ownership boundary.
- Prefer one fix per root cause, with tests that demonstrate the previous unsafe
  behavior is blocked.
- Split PRs when changes are unrelated, such as MQTT TLS defaults vs GitHub Actions
  hardening vs persistence file safety.
- Create gate tasks for deferred low-priority work if any accepted finding is not
  fixed immediately.

### Phase 5: Implementation

- Implement critical and high findings first.
- Keep fixes minimal and idiomatic, matching existing project patterns.
- Add or update tests before broadening behavior.
- Update docs when secure usage, defaults, or threat assumptions change.
- Run targeted tests after each fix group, then full `task pre-pr` before PR.

### Phase 6: Prevention And Maintenance

- Promote useful scanner commands to `Taskfile.yml` tasks.
- Decide whether `security:audit` should be part of `task pre-pr` or only scheduled
  CI, based on runtime and noise.
- Add scheduled security workflows for slow/noisy checks if they are not suitable for
  every PR.
- Document the recurring audit process in `SECURITY.md` or a security guide if the
  project wants it to be public.

## Initial Review Checklist

- MQTT: TLS support, credentials, topic validation, retained sensitive data, ACL
  derivation, wildcard handling, broker failure behavior.
- MCP: dynamic imports, SSE exposure, secret redaction, introspection disclosure,
  scaffolded code safety.
- Filesystem: atomic writes, symlink resistance, path validation, file permissions,
  log sinks, persistence stores.
- Schemas: YAML load safety, `$ref` recursion, schema size limits, generated ACL
  escaping, JSON schema validation failure behavior.
- CI/CD: pinned actions, least privilege permissions, `pull_request_target`, shell
  injection, artifacts, releases, PyPI attestations, SBOMs.
- Dependencies: Python, Rust, GitHub Actions, devcontainer base image, Node/npm use
  in docs deployment.
- Documentation: secure deployment guidance, warning quality, consistency with code.

## Acceptance Criteria

- A threat model exists and is referenced by each finding.
- All deterministic scanner results are triaged as confirmed, false positive, or
  deferred with a beads task.
- All confirmed critical/high findings are fixed before release.
- All confirmed medium/low findings are either fixed or explicitly accepted/deferred
  with rationale and a gate task.
- Every code fix has a regression test or a documented verification command.
- Security-relevant docs are updated for changed defaults or deployment guidance.
- Final PR or PRs pass `task pre-pr` and CI.
- The project has repeatable security commands for the checks worth keeping.

## Decisions

- MQTT TLS remains primarily broker deployment guidance because cosalette does not
  ship or own the MQTT broker and is often used in existing MQTT networks. If a
  first-class client setting provides practical hardening without expanding the
  framework scope too far, it may be added.
- MCP SSE is unnecessary for the current local CLI scope. If it creates avoidable
  security risk, disable it or add proportionate countermeasures.
- Security scanning must run both in `task pre-pr` and scheduled CI.
- SHA pinning is required for all third-party GitHub Actions, including low-privilege
  workflows.
- Prefer one comprehensive PR. Split only if the review/remediation scope becomes
  too large to review safely.

## Proposed Next Step

After approval, start with Option A:

1. Create the beads security audit epic and phase tasks.
2. Create a feature branch for the audit work.
3. Run baseline deterministic checks and scanner evaluation.
4. Produce a findings report with confirmed issues and recommended fix order.
5. Ask for approval on the finding set before implementing broad hardening changes,
  unless a critical issue is discovered that should be fixed immediately.
