# Devcontainer image security review — 2026-11-21

This is an interim review for `cos-nxu6`. The image acceptance groups expired on
2026-11-02. They have not been renewed, and the image scan gate remains failing.

## Refreshed scan

PR #571
[validation run 44894255978](https://github.com/ff-fab/cosalette/actions/runs/44894255978)
rebuilt the previously cached apt upgrade layer and scanned the resulting amd64 image.
The rootless Docker smoke test passed. Trivy reported 392 distinct HIGH/CRITICAL
fingerprints (389 HIGH, 3 CRITICAL) and no secret findings. The previous cached-layer
scan reported 595 fingerprints. Comparing the refreshed inventory with the existing
exact baseline yields 222 fingerprints absent from that baseline; this comparison does
not renew any expired entries. The sanitized vulnerability inventory is retained as the
run's `image-vulnerability-review` artifact for seven days. The scan and this review do
not establish arm64 status.

After removing unused development libraries, PR #571
[validation run 44895017071](https://github.com/ff-fab/cosalette/actions/runs/44895017071)
passed the image build, native C and Rust toolchain checks, and the rootless Docker
smoke test. The new amd64 scan reports 306 fingerprints (303 HIGH and 3 CRITICAL),
including 138 absent from the old baseline. Its sanitized artifact is retained for seven
days; the exact six-field fingerprints are also preserved in
[`security-scan-2026-11-21.tsv`](security-scan-2026-11-21.tsv). No acceptance is implied
by inclusion. All acceptance groups remain expired and the gate fails.

The three CRITICAL package fingerprints are `CVE-2026-60002` in `openssh-client` and
`CVE-2026-6653` in both `libxml2` and `libxml2-dev`. Debian lists the installed Trixie
versions as vulnerable and records no Trixie fix for either advisory at review time. The
OpenSSH issue affects the client when a server changes its host key during rekeying; the
libxml2 issue involves processing malicious XML. Avoid untrusted SSH servers and
untrusted XML while these components remain vulnerable. See the Debian trackers for
[CVE-2026-60002](https://security-tracker.debian.org/tracker/CVE-2026-60002) and
[CVE-2026-6653](https://security-tracker.debian.org/tracker/CVE-2026-6653).

## Remaining work

The 138 changed or new fingerprints in the post-removal scan include 40 Linux header
attributions and 56 Kerberos package attributions across 14 advisory IDs. Header
attribution does not evaluate the host kernel; `cos-dam4` tracks that separate question.
Debian's
[Kerberos tracker](https://security-tracker.debian.org/tracker/source-package/krb5)
lists the 14 advisories as open for Trixie. The image inventory shows curl and OpenSSH
require the Kerberos runtime. PostgreSQL and curl development packages pull in
additional Kerberos administration libraries. PR #571 removes those development
dependencies; the newer scan confirms 86 fewer fingerprints than the previous scan,
while the native toolchain checks passed. The remaining Kerberos runtime requires
advisory-specific review and is not established to be safe by package removal.

The 40 header fingerprints all name `linux-libc-dev 6.12.117-1`. Debian describes that
exact package as userspace development headers installed under `/usr/include`; it is not
a running kernel. This explains the image package attribution, but does not assess the
host kernel, its configuration, or vendor backports. The affected hosts and
advisory-specific applicability remain open under `cos-dam4`. See
[Debian's package description and file list](https://packages.debian.org/trixie/linux-libc-dev).

The post-removal inventory contains Kerberos runtime libraries required by curl and
OpenSSH; it does not list `krb5-kdc` or `krb5-admin-server`. Several of the new
advisories describe attacks against the KDC or `kadmind`, such as
[CVE-2026-119079](https://security-tracker.debian.org/tracker/CVE-2026-119079) and
[CVE-2026-112323](https://security-tracker.debian.org/tracker/CVE-2026-112323). That
inventory narrows those server scenarios for this image, but does not prove all library
code paths unreachable. In particular,
[CVE-2026-71199](https://security-tracker.debian.org/tracker/CVE-2026-71199) concerns
validation of Kerberos authorization data and needs a separate client exposure
assessment. No Kerberos fingerprint is accepted by this interim review.

The scan also reported Syft's embedded `golang.org/x/net` v0.61.0,
`github.com/containerd/containerd/v2` v2.4.1, and `github.com/go-jose/go-jose/v4`
v4.1.5.
[Syft v1.57.2's module file](https://github.com/anchore/syft/blob/v1.57.2/go.mod)
specifies v0.62.0, v2.4.2, and v4.1.6 respectively, matching the scan's fixed versions.
The Dockerfile updates Syft with upstream release asset digests. The rebuilt image in
[run 44895929174](https://github.com/ff-fab/cosalette/actions/runs/44895929174) removed
these three findings and four Go standard-library findings from Syft. The scan reports
299 HIGH/CRITICAL fingerprints, no secrets, and 135 fingerprints absent from the old
baseline. Its normalized inventory is preserved in
[`security-scan-2026-11-21-syft.tsv`](security-scan-2026-11-21-syft.tsv). The expired
baseline still blocks the image gate.

The scan also reported `golang.org/x/net` v0.59.0 and
`github.com/containerd/containerd/v2` v2.4.1 in Docker Buildx. The
[Buildx v0.39.1 module file](https://github.com/docker/buildx/blob/v0.39.1/go.mod)
specifies v0.62.0 and v2.4.2, the scan's respective fixed versions. Docker's Debian
Trixie repository has package 0.39.1-1 for amd64 and arm64, so the pinned package is
updated. A rebuilt image scan must verify the released binary; its module file still
declares other components flagged in the prior scan.

The prior scan also reported `golang.org/x/net` v0.61.0 in Docker Engine's `dockerd` and
`docker-proxy`. Docker Engine 29.10.0's
[module file](https://github.com/moby/moby/blob/docker-v29.10.0/go.mod) specifies
v0.62.0. Its signed Debian Trixie packages for the engine, CLI, and rootless launcher
are available for amd64 and arm64, so the engine pin is updated. Check the rebuilt
binaries and rootless startup before concluding the findings are fixed.

The amd64 image in
[run 44898094540](https://github.com/ff-fab/cosalette/actions/runs/44898094540) passed
its native toolchain and rootless startup checks. Its scan no longer reports the two
Engine `x/net` fingerprints or the three Buildx `x/net` and containerd fingerprints from
the prior scan. It reports 294 HIGH/CRITICAL fingerprints and no secret findings; 131
fingerprints are absent from the old baseline. Its normalized inventory is recorded in
[`security-scan-2026-11-21-docker.tsv`](security-scan-2026-11-21-docker.tsv). The
expired baseline continues to block the gate.

## New Debian findings under review

The following evidence comes from the 294-fingerprint scan and Debian's tracker. It
records exposure and available fixes; it does not add baseline acceptances.

- Git's `CVE-2026-105437`, `CVE-2026-111760`, and `CVE-2026-114466` concern,
  respectively, merge processing of attacker-controlled content, recursive cloning of a
  malicious repository, and fast-import/export behavior. The installed Git is used for
  development, so these are relevant when handling untrusted repositories or streams.
  Debian lists the Trixie source version as vulnerable; see the
  [Git tracker](https://security-tracker.debian.org/tracker/source-package/git).
  `git-man` contains documentation rather than the Git executable, but the Git package
  itself remains installed.
- Expat's `CVE-2026-113351`, `CVE-2026-115506`, `CVE-2026-117610`, and `CVE-2026-118370`
  affect XML parsing, including denial of service and, for oversized inputs, memory
  corruption. Debian lists the scanned Trixie security version as vulnerable; see the
  [Expat tracker](https://security-tracker.debian.org/tracker/source-package/expat).
  Avoid untrusted XML where an installed tool uses this library.
- SQLite's `CVE-2026-119373`, `CVE-2026-119374`, and `CVE-2026-119379` concern FTS5
  queries, deletion from a malformed FTS5 index, and crafted changesets. Debian lists
  the scanned version as vulnerable. Do not process untrusted SQLite databases or
  changesets until the package is updated. See
  [119373](https://security-tracker.debian.org/tracker/CVE-2026-119373),
  [119374](https://security-tracker.debian.org/tracker/CVE-2026-119374), and
  [119379](https://security-tracker.debian.org/tracker/CVE-2026-119379).
- `CVE-2026-120830` is an information leak in libslirp's processing of malformed network
  packets. The image uses slirp for rootless Docker, so the network boundary matters; do
  not treat this as unreachable without a host and container exposure assessment. Debian
  lists the scanned version as vulnerable; see the
  [tracker](https://security-tracker.debian.org/tracker/CVE-2026-120830).
- `CVE-2026-121716` concerns attacker-controlled options to `fzf-tmux`; avoid passing
  untrusted arguments. Debian lists Trixie as vulnerable; see the
  [tracker](https://security-tracker.debian.org/tracker/CVE-2026-121716).
  `CVE-2026-124709` concerns a QUIC server under repeated stream resets. Debian's listed
  fixed-version data does not establish a fix for the scanned OpenSSL version; its
  exposure requires application-specific review. See the
  [tracker](https://security-tracker.debian.org/tracker/CVE-2026-124709).
- Trivy reports `CVE-2026-119741` on `rsync 3.5.0+ds1-0+deb13u2` and `CVE-2026-96512` on
  `sudo 1.9.16p2-3+deb13u5` without fixed versions. Debian's fixed-version tables
  identify those exact Trixie versions as fixed. These two scanner fingerprints
  therefore appear to be stale attribution and should be rechecked against the scanner
  database before any narrow exception. See the
  [rsync tracker](https://security-tracker.debian.org/tracker/CVE-2026-119741) and
  [sudo tracker](https://security-tracker.debian.org/tracker/CVE-2026-96512).

## Remaining Go dependency findings

The scan reports `CVE-2026-78661` in `rootlesskit`, `bd`, `dolt`, and `task`. The
[Go advisory](https://pkg.go.dev/vuln/GO-2026-6860) describes excessive work when
parsing crafted HTML with `golang.org/x/net/html`; the affected module version alone
does not establish that each binary reaches the parser with untrusted input. It also
reports `CVE-2026-116587` in `bd`, `dolt`, and `task`. The
[go-jose advisory](https://github.com/go-jose/go-jose/security/advisories/GHSA-mgcc-cww5-fp2q)
describes CPU exhaustion while parsing attacker-controlled RSA JWKs. Determine each
binary's use and input sources before treating either finding as unreachable.

PR #558 proposes Dolt 2.7.6, whose
[module file](https://github.com/dolthub/dolt/blob/v2.7.6/go/go.mod) upgrades go-jose to
4.1.6 and `klauspost/compress` to 1.18.7, but still declares `x/net` v0.61.0. PR #562
proposes beads 1.3.4, whose
[module file](https://github.com/gastownhall/beads/blob/v1.3.4/go.mod) declares fixed
versions for its reported go-jose, x/net, Thrift, and gRPC findings. Both releases
require rebuilt-image scans and functional checks to verify their actual binaries.
RootlessKit 3.3.0 and Task 3.54.0 were the latest upstream releases found during this
review; continue checking for patched builds.

Reassess every remaining fingerprint and existing acceptance using the final scan,
vendor status, package contents, and reachable use before updating the baseline. Remove
stale rows and use narrow, owned, expiring entries only when their disposition is
supported. Verify the resulting scan has no unreviewed HIGH/CRITICAL findings and no
secrets. Keep `cos-nxu6` open for continuing remediation and `cos-dam4` open for
host-kernel applicability.
