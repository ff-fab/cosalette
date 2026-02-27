# ADR-017: SBOM Generation

## Status

Accepted  **Date:** 2026-02-27

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

## Alternatives Considered

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
