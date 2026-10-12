# Devcontainer vulnerability review — 2026-10-12

The fresh image scan for PR #530 reported three new HIGH fingerprints on
`linux-libc-dev 6.12.111-1`. The package supplies userspace kernel headers; the
devcontainer image does not contain or run the Linux kernel implementation. The exact
fingerprints are temporarily recorded in `scripts/image-scan-baseline.tsv` under the
existing `kernel_headers` group, which expires 2026-10-27. This is a package-attribution
review only. It does not establish that developer or CI host kernels are fixed or
unaffected. The baseline group remains owned by `cos-nxu6`; host applicability remains
open under `cos-dam4`. This review task is tracked by `cos-q8q4`.

| Advisory                                                                     | Kernel issue                                                                                                                     | Debian Trixie status                                         | Review                                                                                                                                        |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| [CVE-2026-98320](https://security-tracker.debian.org/tracker/CVE-2026-98320) | Netfilter flowtable must retain the conntrack reference until the flow is released; upstream describes a conntrack lifetime bug. | `6.12.111-1` vulnerable; fix needed in Trixie.               | The container includes headers only, not the affected netfilter kernel code. Host kernel/module applicability is unassessed and remains open. |
| [CVE-2026-98323](https://security-tracker.debian.org/tracker/CVE-2026-98323) | RDMA/siw fragmented header copying can overrun the receive header and create an out-of-bounds write.                             | `6.12.111-1` vulnerable; fix needed in Trixie.               | The container includes headers only, not the affected RDMA kernel code. Host kernel/module applicability is unassessed and remains open.      |
| [CVE-2026-98378](https://security-tracker.debian.org/tracker/CVE-2026-98378) | BPF link iteration can retain a reference to an unsettled link that is freed, causing a use-after-free.                          | `6.12.111-1` vulnerable; fixed in Debian unstable `7.2.9-1`. | The container includes headers only, not the affected BPF kernel code. Host kernel/module applicability is unassessed and remains open.       |

Debian's
[kernel tracker](https://security-tracker.debian.org/tracker/source-package/linux) shows
all three as vulnerable in Trixie. Upstream stable fixes are recorded in the
[Debian kernel-team tracker for CVE-2026-98320](https://kernel-team.pages.debian.net/kernel-sec/CVE-2026-98320.html),
[CVE-2026-98323](https://kernel-team.pages.debian.net/kernel-sec/CVE-2026-98323.html),
and
[CVE-2026-98378](https://kernel-team.pages.debian.net/kernel-sec/CVE-2026-98378.html).
Remove these baseline rows when Debian publishes a fixed Trixie package. Inventory each
developer and CI host before marking host applicability resolved; the existing
assessment in `.devcontainer/host-kernel-assessment.md` predates these advisories.

## Docker Buildx dependency finding

The rebuilt image for PR #537 reports one additional exact fingerprint:
`usr/libexec/docker/cli-plugins/docker-buildx`, HIGH `CVE-2026-78669`,
`golang.org/x/net` `v0.59.0`, fixed in `v0.60.0`. The official
[Buildx v0.38.0 release](https://github.com/docker/buildx/releases/tag/v0.38.0) was
published on 7 October, before the CVE was published on 8 October, and its
[go.mod pins x/net v0.59.0](https://github.com/docker/buildx/blob/v0.38.0/go.mod). At
review time the release page has no later stable Buildx release to update to.

The [Go vulnerability record](https://pkg.go.dev/vuln/GO-2026-6611) describes excessive
CPU use when a peer repeatedly sends HTTP/2 SETTINGS frames across many streams. Buildx
is retained for container builds and communicates with remote BuildKit/registry
endpoints, so a hostile HTTP/2 peer could cause denial of service when that code path is
exercised. Current CI builds use trusted registry and builder endpoints; this is
exposure context, not proof that the vulnerable code is unreachable. The exact Buildx
`v0.59.0` fingerprint is temporarily covered by the existing `go_binaries` acceptance,
owned by `cos-nxu6` and expiring 2026-11-02. PR #537 upgraded Buildx from `v0.37.0` to
`v0.38.0`; its rebuilt-image scan reports `x/net` `v0.59.0` and no longer reports
`v0.58.0`. The stale Buildx `v0.58.0` baseline row has been removed. Separate `v0.58.0`
fingerprints for `gh`, `rootlesskit`, `bd`, and `dolt` remain covered by their existing
entries. Replace the remaining Buildx acceptance with the first fixed release and
require the rebuilt-image scan to verify `x/net` `v0.60.0` or newer.

## Remove unused librsvg packages

The 12 October scan found `CVE-2026-96889` in `librsvg2-2`, `librsvg2-common`, and
`gir1.2-rsvg-2.0` at Debian version `2.60.0+dfsg-1+deb13u1`. Debian's tracker says
Trixie is still vulnerable at source version `2.60.0+dfsg-1`; the upstream fix is in
2.60.3, and no fixed Trixie package was listed at review time. The flaw is a use-after-
free while parsing SVGs with nested XIncludes and duplicate entities, with potential
denial of service or code execution.

These packages come from the Python devcontainer base image. The project renders brand
assets with CairoSVG, which does not depend on librsvg; the Dockerfile also already
removes the unused ImageMagick stack. Purge the three librsvg packages and remove their
now-stale baseline rows instead of keeping the vulnerable parser. Debian's security
tracker records the affected/fixed versions and upstream fix:
<https://security-tracker.debian.org/tracker/CVE-2026-96889>.
