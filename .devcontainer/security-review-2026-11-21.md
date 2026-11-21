# Devcontainer image security review — 2026-11-21

This is an interim review for `cos-nxu6`. The prior image acceptance groups expired on
2026-11-02. On 2026-11-21, the project owner approved temporary acceptance of the exact
280 fingerprints in the TIFF-removal scan below. These entries expire at the end of
2026-11-21 UTC, less than 24 hours after approval. This accepts the remaining risk for
the interim; it does not assert that vulnerable code is safe. New or changed
fingerprints and any secret finding continue to fail the gate. Keep the input and host
restrictions in this review and replace accepted findings with fixes as soon as
possible. The temporary decision does not complete advisory-specific current review:
server binary presence, some Kerberos descriptions, Go reachability, host conditions,
and arm64 image coverage remain unresolved as detailed below.

## Refreshed scan

PR #571
[validation run 44894255978](https://github.com/ff-fab/cosalette/actions/runs/44894255978)
rebuilt the previously cached apt upgrade layer and scanned the resulting amd64 image.
The rootless Docker smoke test passed. Trivy reported 392 distinct HIGH/CRITICAL
fingerprints (389 HIGH, 3 CRITICAL) and no secret findings. The previous cached-layer
scan reported 595 fingerprints. Comparing the refreshed inventory with the existing
exact baseline yields 222 fingerprints absent from that baseline; this comparison does
did not renew any expired entries. The sanitized vulnerability inventory is retained as
the run's `image-vulnerability-review` artifact for seven days. The scan and this review
do not establish arm64 status.

CI image builds now pass a UTC ISO-week cache key to the apt layer. This refreshes
package upgrades at least weekly while allowing builds within the same week to reuse the
layer. The local `task build:devcontainer` uses the same key; interactive builds can
override `APT_SECURITY_REFRESH` when they need a fresh package upgrade.

After removing unused development libraries, PR #571
[validation run 44895017071](https://github.com/ff-fab/cosalette/actions/runs/44895017071)
passed the image build, native C and Rust toolchain checks, and the rootless Docker
smoke test. The new amd64 scan reports 306 fingerprints (303 HIGH and 3 CRITICAL),
including 138 absent from the old baseline. Its sanitized artifact is retained for seven
days; the exact six-field fingerprints are also preserved in
[`security-scan-2026-11-21.tsv`](security-scan-2026-11-21.tsv). No acceptance is implied
by inclusion. At that point all acceptance groups remained expired and the gate failed.

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
assessment. The exact current runtime fingerprints have only the short temporary
acceptance described above; server-only attribution does not establish client safety.

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
baseline still blocked the image gate at that point.

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
expired baseline continued to block the gate at that point.

The image build's apt inventory also lists `libtiff-dev`, `libtiff6`, and `libtiffxx6`
as no longer required after removal of inherited image-processing development packages.
The Dockerfile removes them explicitly. The image in
[run 44906896085](https://github.com/ff-fab/cosalette/actions/runs/44906896085) passed
its build and native toolchain checks; its scan no longer reports the six TIFF
fingerprints and reports 280 HIGH/CRITICAL findings and no secrets overall. The exact
inventory is in
[`security-scan-2026-11-21-tiff.tsv`](security-scan-2026-11-21-tiff.tsv). The apt
removal also drops the inherited GDK Pixbuf runtime, development package, and tools,
plus `librsvg2-dev`, which depended on the removed image libraries.

## New Debian findings under review

The following evidence comes from the 294-fingerprint scan and Debian's tracker. It
records exposure and available fixes. The exact current fingerprints have only the short
temporary acceptance described above.

The earlier baseline also contains four BlueZ advisories on each of `libbluetooth3` and
`libbluetooth-dev`. The scanned package dependency inventory shows no installed reverse
dependency on the runtime library except its development package. Both are removed from
the development image; projects needing Bluetooth must install their own adapter
dependencies. The image in
[run 44899349833](https://github.com/ff-fab/cosalette/actions/runs/44899349833) passed
its build and native toolchain checks, and its scan no longer reports the eight BlueZ
fingerprints. The scan reports 286 HIGH/CRITICAL fingerprints, with 131 absent from the
expired baseline. The normalized inventory is recorded in
[`security-scan-2026-11-21-bluez.tsv`](security-scan-2026-11-21-bluez.tsv).

The image contains `wget`, flagged for `CVE-2026-58471` and `CVE-2026-58472`. Although
the Dockerfile can purge the inherited package, the GitHub CLI devcontainer feature
installs it again afterward. The scan in
[run 44900275103](https://github.com/ff-fab/cosalette/actions/runs/44900275103) still
reports both findings and 286 HIGH/CRITICAL fingerprints overall. Removing the package
requires a change to that feature installation or a later image layer; it is not
resolved by purging it in the Dockerfile.

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

## Other Debian exposure

The four util-linux advisories reported across its binary packages concern mount
helpers, cgroup namespace entry, or mount options and privileged filesystem state.
Debian lists all four as vulnerable in Trixie. Image package attribution does not
establish the host kernel version, fstab policy, or the privileges available to a
particular user; those conditions require host-specific assessment. See
[CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642),
[CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408),
[CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), and
[CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410).

The scan attributes `CVE-2026-16742` to systemd libraries, but Debian describes a
systemd-homed managed-user issue; `CVE-2026-24882` concerns the separate GnuPG
`tpm2daemon`. Confirm the final image has neither daemon before deciding whether these
package attributions apply. See
[CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) and
[CVE-2026-24882](https://security-tracker.debian.org/tracker/CVE-2026-24882). Other
installed tools have input-dependent exposure: `infocmp` processing malicious terminfo
(`CVE-2025-69720`), Perl Archive::Tar handling malicious headers (`CVE-2026-9538`), and
privileged libacl calls on attacker-controlled path components (`CVE-2026-54369`). See
the Debian trackers for
[ncurses](https://security-tracker.debian.org/tracker/CVE-2025-69720),
[Perl](https://security-tracker.debian.org/tracker/CVE-2026-9538), and
[libacl](https://security-tracker.debian.org/tracker/CVE-2026-54369).

The retained X11 libraries have two additional issues when a client connects to a
malicious X server:
[CVE-2026-88806](https://security-tracker.debian.org/tracker/CVE-2026-88806) in libX11
and [CVE-2026-88807](https://security-tracker.debian.org/tracker/CVE-2026-88807) in
libXrender. Debian lists the scanned Trixie versions as vulnerable with no planned
security update. Do not connect image applications to an untrusted X server; the package
inventory alone does not rule out graphical use.

## Remaining Go dependency findings

The scan reports `CVE-2026-78661` in `rootlesskit`, `bd`, `dolt`, and `task`. The
[Go advisory](https://pkg.go.dev/vuln/GO-2026-6860) describes excessive work when
parsing crafted HTML with `golang.org/x/net/html`; the affected module version alone
does not establish that each binary reaches the parser with untrusted input. It also
reports `CVE-2026-116587` in `bd`, `dolt`, and `task`. The
[go-jose advisory](https://github.com/go-jose/go-jose/security/advisories/GHSA-mgcc-cww5-fp2q)
describes CPU exhaustion while parsing attacker-controlled RSA JWKs. Determine each
binary's use and input sources before treating either finding as unreachable.

For the pinned RootlessKit v3.3.0 amd64 release, the downloaded archive's SHA-256
matches the Dockerfile pin
(`0e757c8f80510d764df8c9a5f169805580b48d02329f0a200900e261ecec27ed`). Its unstripped
`rootlesskit` binary has no `golang.org/x/net/html` symbols in `nm`, including the
parser functions named in the Go advisory. This supports the parser not being linked,
but symbol inspection has compiler-inlining limits and does not establish the arm64
binary's status. It also does not address RootlessKit's separate Go certificate trust
finding.

The scanned Buildx binary reports `CVE-2026-17106` in `moby/go-archive`, whose
[advisory](https://github.com/moby/go-archive/security/advisories/GHSA-hfg8-hc9c-6c3h)
describes archive extraction outside the target directory. Inspection of Docker's amd64
Buildx 0.39.1-1 Debian package found no symbols from `moby/go-archive` or
`docker/docker/daemon` in its unstripped executable. This suggests the affected archive
and daemon functions may not be linked, but it is not a final disposition: match that
package to the scanned image and account for inlining and arm64. It does not assess
other Docker executables. The downloaded Debian package's SHA-256 was
`23ce9f5e567db2c482c4d62d9e3816d6adc27776b1459905ecfef89bbee52008`.

The pinned Task v3.54.0 amd64 archive also matches its Dockerfile SHA-256. Its stripped
executable contains names for go-jose's JWK JSON unmarshalling and RSA private-key
parsing, supporting that relevant code is linked. Whether Task passes
attacker-controlled data to that code is not established; do not dismiss its
`CVE-2026-116587` finding based on the module list alone. Task can also fetch remote
Taskfiles, so a malicious configured HTTP/2 endpoint is relevant to its `CVE-2026-78669`
finding. The separate [HTTP/2 advisory](https://pkg.go.dev/vuln/GO-2026-6611) describes
CPU exhaustion. The reported Go `CVE-2026-97031` and `CVE-2026-78667` concern
respectively a TLS server and an HTTP file server; their applicability depends on
whether each affected executable serves those requests, which has not yet been
established.

PR #558 proposes Dolt 2.7.6, whose
[module file](https://github.com/dolthub/dolt/blob/v2.7.6/go/go.mod) upgrades go-jose to
4.1.6 and `klauspost/compress` to 1.18.7, but still declares `x/net` v0.61.0. PR #562
proposes beads 1.3.4, whose
[module file](https://github.com/gastownhall/beads/blob/v1.3.4/go.mod) declares fixed
versions for its reported go-jose, x/net, Thrift, and gRPC findings. Both releases
require rebuilt-image scans and functional checks to verify their actual binaries.
RootlessKit 3.3.0 and Task 3.54.0 were the latest upstream releases found during this
review; continue checking for patched builds.

An independent scan of the pinned Dolt 2.7.6 image in PR #558 found two HIGH findings in
its actual executable and zero secret matches. The findings are `CVE-2026-78661` in
x/net v0.61.0, and `CVE-2026-84445` in gRPC v1.83.1. The latter concerns xDS server
configuration according to the [Go advisory](https://pkg.go.dev/vuln/GO-2026-6443);
whether Dolt exposes that path remains unverified. The newer binary removes the other
previously reported Dolt fingerprints, but these two remain for review. The extracted
amd64 executable has SHA-256
`b0f849b95e08dde31c94e1d65603c016276ed8206d5ee4cb10ee8e44f9626d0b`. Its retained Go
function-name table contains HTTP/2 symbols but no x/net HTML parser or gRPC
`xds.NewGRPCServer` symbols. This supports the relevant entry points being absent,
subject to inlining and alternate paths; it does not prove either embedded module is
fixed or establish arm64 status. See the
[HTML advisory](https://pkg.go.dev/vuln/GO-2026-6860) and
[gRPC advisory](https://github.com/grpc/grpc-go/security/advisories/GHSA-2v4p-qf9q-27wj).

The beads 1.3.4 image in
[PR #562 run 44907404500](https://github.com/ff-fab/cosalette/actions/runs/44907404500)
reports no HIGH/CRITICAL findings for the `bd` executable and no secret matches in the
image. That branch predates the other package removals here and must be rescanned after
it is updated against the merged image work.

Reassess every remaining fingerprint and existing acceptance using the final scan,
vendor status, package contents, and reachable use before renewing the baseline. Remove
stale rows and use narrow, owned, expiring entries only when their disposition is
supported. Verify the resulting scan has no unreviewed HIGH/CRITICAL findings and no
secrets. Keep `cos-nxu6` open for continuing remediation and `cos-dam4` open for
host-kernel applicability.

## Beads 1.3.4 update

PR #562 updates the downloaded beads executable from 1.3.1 to 1.3.4. Both Linux archive
checksums match the upstream release checksums. The downloaded amd64 executable reports
Go 1.26.10, Thrift 0.24.0, go-jose 4.1.6, x/net 0.62.0, and gRPC 1.83.2 in its build
metadata. Those versions meet the fixes listed for all ten prior beads fingerprints,
which have therefore been removed from the exact acceptance baseline. The image scan
must still verify the installed executable and identify any new or remaining findings.
