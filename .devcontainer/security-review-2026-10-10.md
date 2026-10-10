# Devcontainer vulnerability review — 2026-10-10

The PR 517 image scan found 47 new HIGH findings after updating Docker Engine to 29.9.0:
13 Debian package attributions and 34 findings in nine prebuilt Go tools. The report
contains 311 HIGH/CRITICAL findings total and no secrets. Docker's update removed 11 of
the prior findings. The remaining exact versions and fingerprints are recorded in
`scripts/image-scan-baseline.tsv`; temporary acceptances are owned by `cos-nxu6` and
expire as specified in `scripts/image-scan-acceptance.json`.

## Debian packages (13 findings)

| Advisory       | Installed package                            | Review and disposition                                                                                                                                                                                                                                                                                                                                                              |
| -------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CVE-2026-77214 | `libexpat1`, `libexpat1-dev` 2.8.3-1~deb13u1 | Debian marks Trixie vulnerable; the tracker lists a fix in sid only. This is a heap overread in Expat. The library remains present, so exposure is real if a tool parses attacker-controlled XML. Keep as temporary `debian_pending` until Debian publishes a Trixie fix or the packages are removed. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-77214). |
| CVE-2026-88647 | Four GnuTLS packages at 3.8.9-3+deb13u4      | Hostname verification can fall back with a crafted certificate. Debian currently lists no fixed release, including sid. GnuTLS is installed and used by image tools; certificate validation risk remains. Keep as temporary `debian_pending`, review on vendor update. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-88647).                                |
| CVE-2026-88648 | Same four GnuTLS packages                    | Incomplete X.509 constraints can allow a subordinate CA to bypass cross-domain PKI constraints. Debian currently lists no fixed release, including sid. This is a meaningful trust-validation risk; temporary acceptance does not imply it is harmless. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-88648).                                               |
| CVE-2026-97528 | `linux-libc-dev` 6.12.111-1                  | Kernel qla2xxx NVMe error-path use-after-free. The scanner attributes the finding to installed UAPI/header files; those are not the running kernel implementation. This does not establish host safety or patch status. Host applicability remains tracked by `cos-dam4`. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-97528).                             |
| CVE-2026-98222 | `linux-libc-dev` 6.12.111-1                  | Kernel encrypted-keys integer overflow; same header-only attribution limitation and outstanding host assessment. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-98222).                                                                                                                                                                                      |
| CVE-2026-98364 | `linux-libc-dev` 6.12.111-1                  | Kernel xfrm RCU use-after-free; same header-only attribution limitation and outstanding host assessment. [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-98364).                                                                                                                                                                                              |

The two Expat rows and eight GnuTLS rows use the existing `debian_pending` group
(expires 2026-10-27). Three kernel header rows use `kernel_headers` with the same
expiry. These are time-limited review decisions, not claims that the package code is
patched.

## Prebuilt Go tools (34 findings)

The findings are CVE-2026-78667 and CVE-2026-97031 in Go's `net/http`/`crypto/tls`
standard library, and CVE-2026-78669 in HTTP/2 (both standard library and
`golang.org/x/net` where present). The Go vulnerability database lists standard-library
fixes in Go 1.26.9 and 1.27.2, and the x/net fix in v0.60.0. The issues include denial
of service from crafted Range requests, HTTP/2 SETTINGS traffic, and ECH extension
processing. [GO-2026-6607](https://pkg.go.dev/vuln/GO-2026-6607),
[GO-2026-6609](https://pkg.go.dev/vuln/GO-2026-6609),
[GO-2026-6611](https://pkg.go.dev/vuln/GO-2026-6611).

Affected exact binaries in this scan: `gh` (Go 1.27.1, x/net 0.58.0), `rootlessctl`
(1.26.8), `rootlesskit` and `rootlesskit-docker-proxy` (1.26.8; rootlesskit x/net
0.58.0), `docker-buildx` (1.26.8, x/net 0.58.0), `bd` (1.26.7, x/net 0.58.0), `dolt`
(1.26.2, x/net 0.58.0), `syft` (1.26.8, x/net 0.59.0), and `task` (1.27.1, x/net
0.59.0). The baseline records the individual CVE, component, and installed version for
every finding.

At review time these are prebuilt upstream binaries; their latest available releases do
not contain the Go fixes. Docker Engine was the exception: its upstream 29.9.0 release
includes Go 1.26.9 and x/net 0.60.0, so the Docker pins were updated and its findings
disappeared. Other tools can process repository, image, network, or registry input;
crafted input can trigger denial of service. Keep the exact remaining findings
temporarily in `go_binaries` through 2026-11-02. Update each pinned tool as soon as its
publisher ships patched dependencies, then remove only the corresponding baseline rows.
Recheck upstream releases before expiry; do not treat the development-image context as
proof of non-exploitability.

## Follow-up

`cos-nxu6` owns the image baseline review. Recheck vendor package status and upstream
tool releases before the listed expiries. The Docker 29.9.0 pin is the remediation
already shipped in this PR. Host-kernel assessment remains separate and open under
`cos-dam4`.
