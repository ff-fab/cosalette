# cos-ms5p: seven-day renewal of the 2026-11-21 acceptance groups

Prepared on 22 November 2026 for cos-ms5p. All seven acceptance groups in
`scripts/image-scan-acceptance.json` expired at the end of 2026-11-21 UTC, as set by the
[interim review](security-review-2026-11-21.md). Since then the image scan gate has
failed on every pull request that changes `.devcontainer/`, for example
[PR #579 run 45000683736](https://github.com/ff-fab/cosalette/actions/runs/45000683736).
The gate printed `expired group` for every baseline row. It did not report any new
fingerprint.

## Evidence

[PR #582 run 45001146854](https://github.com/ff-fab/cosalette/actions/runs/45001146854)
scanned the amd64 image built from `python:3.14@sha256:80dc8003…`, the current digest of
the 3.14 tag. It reported 246 HIGH/CRITICAL fingerprints and no secrets. The PR #579
image (Python 3.15) has the same 246 fingerprints. Every one matches an existing
baseline row exactly, so the scan contains no new advisory, version or fix.

Fixes were checked before renewal:

- **Debian packages:** none of the 230 Debian fingerprints has a fixed version. The
  image already upgrades cached packages.
- **Go executables:** 14 of the 16 rows list fixed module versions. Their tools are
  already on the latest upstream releases, none of which ships those fixes: Dolt 2.7.6,
  Task v3.54.0, RootlessKit v3.3.0 and the Buildx 0.39.1 Debian package.

Nine stale rows were removed from the baseline because the current image no longer
contains them: the rsync and sudo rows, and CVE-2026-97054 (Go stdlib) for RootlessKit,
bd, Dolt, Syft and Task.

## Decision

The disposition, owner (`cos-nxu6`) and restrictions in the
[interim review](security-review-2026-11-21.md) are unchanged. Every group now expires
on 2026-11-29. This is a deliberately short window: it unblocks devcontainer PRs and
keeps the full reassessment due under `cos-nxu6`. Any fix that lands upstream before
then should replace its rows. Merging this file is the project owner's acceptance of the
renewal.
