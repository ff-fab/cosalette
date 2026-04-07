---
status: Accepted
date: 2026-04-02
impact: low
tags: [release, security]
---

# ADR-026: Immutable Releases

## Status

Accepted **Date:** 2026-04-02

## Context

cosalette publishes wheels to PyPI and TestPyPI via a GitHub Actions release workflow
(ADR-017). The workflow attaches a CycloneDX SBOM to the GitHub Release as a
downloadable asset. Two mechanisms existed that violated release immutability:

1. **`--clobber` flag on SBOM upload** — `gh release upload ... --clobber` overwrites
   a previously attached SBOM, silently replacing a release artifact after publication.
2. **`workflow_dispatch` re-publish path** — a manual trigger allowed rebuilding and
   re-publishing an existing tag. While PyPI rejects duplicate version uploads,
   TestPyPI accepted them (via `skip-existing: true`), and the SBOM re-attach relied
   on `--clobber`.

Together these created a window where a published release's artifacts could be
modified post-publication — undermining supply chain integrity guarantees built on
SLSA provenance attestations (ADR-017) and PEP 740 Sigstore attestations.

GitHub offers an "immutable releases" repository setting that prevents overwriting or
deleting release assets after creation, providing a platform-level enforcement of
artifact immutability.

## Decision

Enforce immutable releases through three changes:

1. **Remove `--clobber`** from the SBOM upload step in the release workflow.
2. **Remove the `workflow_dispatch` re-publish path** entirely — delete the trigger
   and all `|| github.event_name == 'workflow_dispatch'` conditions.
3. **Enable GitHub's immutable releases** repository setting for platform-level
   enforcement.

Manual intervention (e.g., `gh release upload`) remains available for exceptional
cases. The escape hatch is intentionally manual, not automated.

## Decision Drivers

- Supply chain integrity — release artifacts must not change after publication
- Alignment with SLSA and Sigstore attestation guarantees (ADR-017)
- Simplicity — the `workflow_dispatch` path added complexity for marginal value
- PyPI already enforces version immutability; GitHub Releases should match

## Considered Options

### Option A: Remove `workflow_dispatch` entirely (chosen)

- **Advantages**: Simplest, eliminates all re-publish paths, aligns with immutability
  principle
- **Disadvantages**: No automated escape hatch for transient failures

### Option B: Gate `workflow_dispatch` with environment protection

- **Advantages**: Preserves an emergency re-publish path with reviewer approval
- **Disadvantages**: Contradicts immutability principle, adds environment configuration
  complexity, the re-publish to PyPI fails anyway (duplicate version rejected)

### Option C: Keep `workflow_dispatch` but remove `--clobber` only

- **Advantages**: Minimal change, manual trigger still useful for TestPyPI
- **Disadvantages**: Re-publish path is misleading — it succeeds on TestPyPI but fails
  on PyPI, creating partial re-publish states

## Decision Matrix

| Criterion                  | Option A | Option B | Option C |
| -------------------------- | -------- | -------- | -------- |
| Supply chain integrity     | 5        | 3        | 2        |
| Simplicity                 | 5        | 3        | 4        |
| Emergency recovery         | 3        | 4        | 4        |
| Consistency (PyPI parity)  | 5        | 4        | 2        |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Release artifacts are immutable at both the platform level (GitHub) and registry
  level (PyPI)
- SLSA provenance and Sigstore attestations are meaningful — the artifacts they
  describe cannot be replaced
- Release workflow is simpler — fewer conditional paths, no `workflow_dispatch` inputs
- Aligns with ADR-017's "build-once publish-twice" principle

### Negative

- No automated recovery for transient SBOM attachment failures — requires manual
  `gh release upload` (acceptable given rarity)
- Cannot re-trigger a full release pipeline for an existing tag — a new patch release
  is required instead

_2026-04-02_
