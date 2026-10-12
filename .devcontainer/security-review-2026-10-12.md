# Devcontainer vulnerability review — 2026-10-12

The fresh image scan for PR #530 reported three new HIGH fingerprints on
`linux-libc-dev 6.12.111-1`. The package supplies userspace kernel headers; the
devcontainer image does not contain or run the Linux kernel implementation. The exact
fingerprints are temporarily recorded in `scripts/image-scan-baseline.tsv` under the
existing `kernel_headers` group, which expires 2026-10-27. This is a package-attribution
review only. It does not establish that developer or CI host kernels are fixed or
unaffected. Host applicability remains open under `cos-q8q4`.

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
