---
status: Accepted
date: 2026-10-10
impact: moderate
tags: [security, dependencies, release]
---

# ADR-090: Keep devcontainer tool checksums current under Renovate

## Status

Accepted **Date:** 2026-10-10 | Amended **Date:** 2026-10-10

## Context

The devcontainer Dockerfile installs several tools from GitHub releases and verifies each download against a pinned `*_SHA256_AMD64` / `*_SHA256_ARM64` ARG. Renovate (hosted Mend app, `customManagers:dockerfileVersions`) bumps the paired `*_VERSION` ARG but not the hashes, so every such PR fails `sha256sum -c` in the DevContainer Build until someone edits the hashes by hand.

The hosted app does not run `postUpgradeTasks`, and fetching checksums at build time would remove the reviewed, pinned value from git. Publishers differ in what they offer: syft and opencode publish immutable releases with GitHub's release attestation, beads publishes SLSA provenance and a checksums file, go-task publishes an unsigned checksums file, and dolt publishes only the release asset digest but also ships an official multi-arch image whose `dolt` binary is byte-identical to the release tarball's static binary. ADR-017 (syft for SBOMs) and ADR-026 (immutable releases) set the surrounding supply-chain baseline.

## Decision

Keep every Renovate-bumped download pinned to a hash in git, and choose per tool how that hash is produced:

1. **Digest-pinned image** — where an official image ships the same self-contained binary and offers no weaker verification than the release asset, install it with `COPY --from=<image>:<tag>@sha256:<digest>`; Renovate updates tag and digest natively. Applies to **dolt** (`dolthub/dolt`), alongside the existing uv image.
2. **Hash-writing workflow** — for the remaining GitHub-release tools (**syft, go-task, beads, opencode**), `.github/workflows/renovate-checksums.yml` runs on `pull_request_target` for same-repository `renovate/*` PRs opened by `renovate[bot]`. It runs only default-branch code: `scripts/renovate_checksums.py` reads the PR's Dockerfile through the API as data, downloads the bumped assets, requires the local SHA-256 to equal the GitHub release asset digest and the publisher's checksums file, verifies the release attestation (`gh release verify-asset`) or SLSA provenance (`gh attestation verify` with signer workflow and tag) where the publisher offers it, and fails closed on any mismatch. The new hashes are committed to the PR branch through the contents API with the release GitHub App token, scoped to `contents: write` on this repository, so the DevContainer Build re-runs on the reviewable diff. `renovate.json` lists the App's bot e-mail in `gitIgnoredAuthors`.
3. **Manual** — tools without a Renovate pin stay manual until cos-zfe6 decides their tracking (RootlessKit, cargo-deny). Tools without a hash ARG keep their existing verification (apt signed repositories for Docker/containerd/buildx, rustup and Cargo lockfile checksums, the Claude Code installer manifest).

These updates are never automerged and keep the 7-day `minimumReleaseAge`. Renovate's version format must match how the Dockerfile consumes the ARG: `SYFT_VERSION` strips the tag's `v` via `extractVersion`, and `RUST_VERSION` stays `major.minor` via `extractVersion=^(?<version>\d+\.\d+)\.\d+$`.

## Decision Drivers

- A Renovate bump must pass the DevContainer Build without a manual checksum edit
- The verified hash stays a reviewed value in git, never fetched at build time
- Use the strongest verification each publisher offers and fail closed
- No code from a PR branch runs with write credentials
- Least privilege for any token that writes to PR branches

## Considered Options

### Option 1: Per-tool mix of digest-pinned images and a hash-writing workflow (chosen)

Digest-pinned COPY --from where an equivalent official image exists, a default-branch workflow that verifies releases and commits hashes for the other GitHub-release tools, manual for untracked tools.

- *Advantages:* Each tool uses its strongest available verification; Renovate PRs become buildable with the hash change visible in the diff; Image-based tools need no workflow at all
- *Disadvantages:* A pull_request_target workflow and an App token write to Renovate branches; Two mechanisms instead of one

### Option 2: Hash-writing workflow for every GitHub-release tool

Keep all downloads as release tarballs, including dolt, and let the workflow refresh every hash.

- *Advantages:* One mechanism for all tools; No new registry dependency
- *Disadvantages:* dolt would rely on the asset digest alone, with no more assurance than the digest-pinned image; More code paths in the workflow

### Option 3: Keep all checksum updates manual

Maintainers copy new hashes into each Renovate PR by hand.

- *Advantages:* No new workflow or token use; Nothing writes to Renovate branches
- *Disadvantages:* Every Dockerfile bump fails CI until edited by hand; A hand-copied hash gets no signature or attestation check

## Decision Matrix

| Criterion | Per-tool mix of digest-pinned images and a hash-writing workflow | Hash-writing workflow for every GitHub-release tool | Keep all checksum updates manual |
| --- | --- | --- | --- |
| Renovate PR builds without manual edits | 5 | 5 | 1 |
| Verification strength per tool | 5 | 4 | 2 |
| Credential exposure | 3 | 3 | 5 |
| Maintenance effort | 4 | 4 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Renovate bumps of dolt, syft, go-task, beads and opencode reach a green DevContainer Build without hand-edited hashes
- Attestation and provenance checks now run before a syft, opencode or beads hash is accepted
- Workflow and parser changes only take effect after review on the default branch

### Negative

- The release App can push to Renovate branches; its token use must stay scoped to contents on this repository
- go-task and dolt still rest on the publisher's unsigned release metadata plus the 7-day age and human review
- A Renovate rebase drops the hash commit, so the workflow runs again after each rebase
- The dolt image comes from Docker Hub and is subject to its pull limits

## Amendment (2026-10-10) — Additive

**Rationale:** Point 3 left RootlessKit and cargo-deny manual until cos-zfe6 decided their tracking. RootlessKit is pinned because of a CVE, so an untracked pin is a risk. Docker, containerd and buildx were tracked from Docker Hub and GitHub releases but installed as apt packages that appear later and do not always use Debian revision -1 (containerd.io 2.4.1 exists only as 2.4.1-2), so Renovate could propose versions apt cannot install.

### Additional Sub-Decision: Track every pinned tool from the source it is installed from

Every `*_VERSION` ARG in `.devcontainer/Dockerfile` carries a `# renovate:` comment whose datasource matches how the tool is installed. No pinned tool stays manual.

- **RootlessKit** (`rootless-containers/rootlesskit`) and **cargo-deny** (`EmbarkStudios/cargo-deny`) use `github-releases` and join the hash-writing workflow. RootlessKit is verified against the asset digest, `SHA256SUMS` and the release attestation. cargo-deny is verified against the asset digest and its per-asset `.sha256` sidecar.
- **Docker, containerd and buildx** use the `deb` datasource against Docker's apt repository (`suite=trixie`, `binaryArch=amd64`). Each ARG holds the package version with its Debian revision but without epoch or distro suffix (for example `2.3.6-1`). A bump is proposed only once apt can install it. The suite in the comments must follow the base image's Debian release.
- The `deb` datasource publishes no release timestamps, so a `renovate.json` rule sets `minimumReleaseAgeBehaviour: timestamp-optional` for it. Otherwise `minimumReleaseAge` would keep these updates pending forever.
- Tools without a hash ARG keep their existing verification, as in point 3: rustup and Cargo lockfile checksums, and the Claude Code installer manifest. Unpinned Debian utilities (DL3008) stay deliberately manual and are updated through the base image.

### Additional Positive Consequences

- The CVE-driven RootlessKit pin and cargo-deny get Renovate PRs with verified hashes instead of relying on someone remembering them
- Docker apt bumps only propose versions already published in the apt repository (amd64 index; arm64 is published alongside)

### Additional Negative Consequences

- Docker apt updates skip the 7-day release-age delay because the apt repository has no timestamps; human review remains the gate
- The trixie suite in the deb registryUrl must be updated by hand when the base image moves to a new Debian release
- cargo-deny's sidecar comes from the same release as the asset, so it adds integrity but not provenance

## Amendment (2026-10-10) — Minor

!!! note "Editorial note (2026-10-10)"
    The original Decision point 3's reference to RootlessKit and cargo-deny as manual is superseded by the additive amendment below, which tracks both with Renovate.
