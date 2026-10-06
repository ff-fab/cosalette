---
status: Accepted
date: 2026-02-27
impact: low
tags: [security, packaging]
---

# ADR-017: SBOM Generation

## Status

Accepted  **Date:** 2026-02-27 | Amended **Date:** 2026-10-05

## Context

- Supply chain security is increasingly important (US EO 14028, EU CRA)
- cosalette is distributed as a PyPI wheel — consumers need to assess dependency risk
- No SBOM, attestation, or supply-chain tooling existed in the project
- Two dominant SBOM formats: CycloneDX (OWASP, security-focused) and SPDX (Linux
  Foundation, compliance-focused, ISO 5962:2021)
- Multiple generator tools exist: syft (Anchore), cdxgen, cyclonedx-python-lib, trivy
- The release workflow previously built the wheel independently in each publish job

## Decision

1. **Format**: CycloneDX JSON — security-oriented, native VEX support, simpler schema,
   better fit for an IoT library. SPDX can be added later as a one-flag change.

2. **Generator**: syft (Anchore) — supports Python wheels natively, multi-format output,
   single binary, also handles OCI images if needed later.

3. **Integration points**:
   - **DevContainer**: syft binary installed in Dockerfile (available for local
     `task sbom`)
   - **Taskfile**: `task sbom` builds the wheel and generates CycloneDX JSON
   - **Release workflow**: After building the wheel, syft generates a CycloneDX SBOM
     that is attached to the GitHub Release as a downloadable asset

4. **Build-once publish-twice**: The release workflow is refactored to build the wheel
   once and upload the same artifact to TestPyPI and PyPI, ensuring the SBOM accurately
   describes the published artifact.

## Decision Drivers

- Providing supply chain transparency for consumers of the published wheel
- Aligning with Python ecosystem and OWASP security tooling conventions
- Minimising complexity — one format, one generator, automated in release pipeline
- Preserving ability to add SPDX later without architectural changes

## Considered Options

### SPDX instead of CycloneDX

- **Advantages**: ISO standard (5962:2021), required by some US government agencies
- **Disadvantages**: More verbose schema, license/compliance focus vs. security focus
- **Rejected because**: Current consumers are personal IoT bridges, not regulated
  entities. CycloneDX's security orientation is a better fit. Adding SPDX output is
  trivial (one syft flag) if demand arises.

### SBOM inside the wheel

- **Rejected because**: Not standard practice. SBOMs are separate artifacts alongside
  the distribution, not embedded in it. PyPI does not host SBOMs.

### No SBOM (status quo)

- **Rejected because**: Supply chain transparency is becoming non-optional for published
  libraries. Even for a small project, this is minimal effort with the right tooling.

## Consequences

### Positive

- Consumers can assess cosalette's dependency tree for known vulnerabilities
- CycloneDX JSON is machine-parseable by downstream tools (Dependency-Track, Grype,
  Trivy)
- Single build ensures wheel checksums match across TestPyPI and PyPI
- syft in devcontainer enables local SBOM generation during development
- Foundation for future supply chain improvements (attestations, SLSA provenance, VEX)

### Negative

- DevContainer image grows by ~50MB (syft binary)
- Release workflow gains one additional step (minimal complexity)
- SBOM must be regenerated if dependencies change (automated via release workflow)

### Neutral

- SPDX output can be layered on later without architectural changes
- PyPI attestations (PEP 740) and SLSA provenance are deferred to a future ADR
- DevContainer image SBOM is deferred (not relevant to end users)

## Amendment (2026-10-05) — Additive

**Rationale:** syft reported zero components for a cosalette .whl because it does not look inside the wheel archive. ADR-087 platform wheels also carry a native cosalette-health binary whose Rust crates were invisible to the SBOM.

### Additional Sub-Decision: Scan the unpacked wheel

`scripts/wheel-sbom.sh` unpacks the wheel and runs syft on the directory with `--source-name cosalette --source-version <wheel version>` and per-file entries disabled. The release workflow, PR wheel validation and `task sbom` use it. The SBOM covers the manylinux x86_64 platform wheel.

### Additional Sub-Decision: Build the probe with cargo-auditable

`scripts/bundle-health-probe.sh` builds `cosalette-health` with `cargo auditable build`, which embeds the crate dependency list in a linker section that survives `strip`. syft reads it as `pkg:cargo/...` components, and grype can match them. CI installs a pinned `cargo-auditable` (`CARGO_AUDITABLE_VERSION` in `rust-wheels.yml`), and so does the devcontainer. The Linux build explicitly adds Cargo's install directory to PATH. `wheel-sbom.sh` fails if a wheel bundles the probe but the SBOM lists none of its crates.

### Additional Considered Options

**cargo-cyclonedx SBOM per crate**

Generate a CycloneDX file from Cargo.lock with cargo-cyclonedx and merge it with the syft output.

- *Advantages:* No change to how the binary is built
- *Disadvantages:* Describes the lock file, not the shipped binary; Needs a second tool and an SBOM merge step

### Additional Positive Consequences

- The release SBOM lists the cosalette package and every crate linked into the cosalette-health probe
- PR CI validates SBOM generation before publication; shell tests cover missing probe crate rejection

### Additional Negative Consequences

- Every platform wheel job compiles cargo-auditable first (inside the manylinux/musllinux container on Linux)
- The pyo3 extension's crates are still not listed: maturin has no cargo-auditable option, and syft ignores the PEP 770 SBOM maturin writes to dist-info (tracked as cos-rl4a)
