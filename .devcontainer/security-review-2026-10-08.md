# Development image security review — 8 October 2026

PR 506's rebuilt image reports three new HIGH fingerprints, all attributed to
`linux-libc-dev 6.12.111-1`, with no fixed version in the scan. Each exact row is
accepted under `kernel_headers`, owned by cos-nxu6, only until **27 October 2026**. No
other fingerprint, expiry or enforcement rule changes.

[Debian's package description](https://packages.debian.org/trixie/linux-libc-dev)
identifies this package as userspace development headers. The previously inspected
package inventory contains headers, documentation and metadata, rather than a running
kernel or loadable driver; see [remediation](security-remediation.md). This acceptance
concerns image package attribution, not host-kernel safety.

## Advisory-specific review

| Advisory                                                                     | Affected implementation and prerequisite                                                                                                                                                                        | Image decision                                                                         | Host follow-up                                                                                                                                                                           |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [CVE-2026-89811](https://security-tracker.debian.org/tracker/CVE-2026-89811) | AMD KFD MES queue removal can leave stale TLB mappings during memory migration, allowing in-flight DMA to access unmapped memory. Inspect `HSA_AMD`, `DRM_AMDGPU`, loaded drivers and exposed GPU devices.      | Header attribution only; no executable KFD implementation is supplied by this package. | **Open / unknown** for workstation and CI: verify vendor backports and actual GPU reachability, including device access from nested namespaces.                                          |
| [CVE-2026-90111](https://security-tracker.debian.org/tracker/CVE-2026-90111) | IPv6 multicast route reporting can reuse a freed destination after an unresolved multicast packet is queued and its route deleted. Inspect `IPV6`, `IPV6_MROUTE`, multicast routing and namespace capabilities. | Header attribution only; the host supplies the network implementation.                 | **Open / unknown** for workstation and CI: verify vendor backports and multicast routing exposure in user-owned network namespaces. Rootless Docker does not establish non-reachability. |
| [CVE-2026-90315](https://security-tracker.debian.org/tracker/CVE-2026-90315) | PCI legacy sysfs I/O and memory handlers omit lockdown checks. Inspect `PCI`, `HAVE_PCI_LEGACY`, architecture, lockdown state and writable legacy sysfs files.                                                  | Header attribution only; no executable PCI implementation is supplied by this package. | **Open / unknown** for workstation and CI: establish whether legacy handlers are built and accessible, then verify vendor backports. No architecture-based exemption is assumed.         |

At review time the three linked Debian trackers mark trixie security's Linux 6.12.111-1
vulnerable and list unstable 7.2.6-1 as fixed. That does not establish a fixed WSL or
GitHub runner build. Advisory-specific host assessments remain with cos-dam4 and owner
cos-nxu6; [host assessment](host-kernel-assessment.md) records the outstanding evidence.
Recheck each exact row before expiry and remove it when a patched package ships or its
attribution changes.

## Validation scope

The CI artifact is a sanitized vulnerability-only report. Rechecking its exact
fingerprints validates vulnerability acceptance only: it cannot establish zero secrets.
A fresh image scan must still run the original secret gate and enforce changed versions,
newly available fixes, unreviewed findings and expired entries.

The existing policy passes against PR 506's sanitized report: **264 HIGH/CRITICAL
fingerprints, zero unreviewed**. `task test:security:docker:policy` and
`task test:devcontainer` pass. `task lint` passes with access to the existing uv cache
(the sandbox's default cache is read-only).
