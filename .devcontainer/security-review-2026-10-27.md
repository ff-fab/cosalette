# Devcontainer vulnerability review — 2026-10-27

The fresh PR #554 image scan reported 295 HIGH/CRITICAL findings and no secrets. After
matching the existing exact-version baseline, 83 fingerprints were unreviewed. This
review adds only those 83 exact identities to the existing acceptance groups; the other
baseline rows are preserved. The added rows are not statements that the underlying
software is patched or non-exploitable. They expire 2026-11-02 and remain tracked by
`cos-nxu6`.

## Linux userspace headers

The scan attributes 60 fingerprints to `linux-libc-dev 6.12.113-1`, including CRITICAL
`CVE-2026-93221` and `CVE-2026-99083`. The exact advisory IDs are in
`scripts/image-scan-baseline.tsv` under `kernel_headers`. They cover `CVE-2013-7445`,
`CVE-2019-19449`, `CVE-2019-19814`, `CVE-2021-3847`, `CVE-2021-3864`, `CVE-2024-21803`,
`CVE-2024-58015`, `CVE-2025-38137`, `CVE-2025-38187`, `CVE-2025-38421`,
`CVE-2025-38636`, `CVE-2025-39859`, `CVE-2025-39862`, `CVE-2025-39958`,
`CVE-2025-40025`, `CVE-2025-68174`, `CVE-2025-68735`, `CVE-2026-100072`,
`CVE-2026-23102`, `CVE-2026-23208`, `CVE-2026-23327`, `CVE-2026-31493`,
`CVE-2026-31568`, `CVE-2026-43185`, `CVE-2026-46130`, `CVE-2026-46181`,
`CVE-2026-46279`, `CVE-2026-52991`, `CVE-2026-53000`, `CVE-2026-53091`,
`CVE-2026-53109`, `CVE-2026-53118`, `CVE-2026-53277`, `CVE-2026-53330`,
`CVE-2026-63879`, `CVE-2026-64283`, `CVE-2026-64389`, `CVE-2026-64400`,
`CVE-2026-68409`, `CVE-2026-68426`, `CVE-2026-68470`, `CVE-2026-72042`,
`CVE-2026-72098`, `CVE-2026-74269`, `CVE-2026-74520`, `CVE-2026-74752`,
`CVE-2026-89631`, `CVE-2026-89675`, `CVE-2026-89788`, `CVE-2026-89811`,
`CVE-2026-90111`, `CVE-2026-90206`, `CVE-2026-90315`, `CVE-2026-93221`,
`CVE-2026-98084`, `CVE-2026-98364`, `CVE-2026-98466`, `CVE-2026-98715`,
`CVE-2026-98853`, and `CVE-2026-99083`.

`linux-libc-dev` provides userspace kernel headers under `/usr/include` and
`/usr/lib/linux/uapi`; the image does not contain the affected running kernel code. In
particular, CVE-2026-93221 concerns an NFS server race and CVE-2026-99083 concerns an
out-of-bounds write in ksmbd. Their container package attribution does not answer
whether a developer or CI host kernel is affected. Host applicability remains open in
`cos-dam4`; this review makes no host-safety claim. See the
[Debian Linux tracker](https://security-tracker.debian.org/tracker/source-package/linux),
[CVE-2026-93221](https://security-tracker.debian.org/tracker/CVE-2026-93221),
[CVE-2026-99083](https://lists.openwall.net/linux-cve-announce/2026/10/23/215), and
[CVE-2026-100072](https://security-tracker.debian.org/tracker/CVE-2026-100072).

## curl and libcurl

`CVE-2026-12064` and `CVE-2026-8458` are reported against `curl`, `libcurl3t64-gnutls`,
`libcurl4-openssl-dev`, and `libcurl4t64` at `8.14.1-2+deb13u7`. Debian's Trixie tracker
lists no fixed package for these advisories at review time. curl is installed and is
used to fetch build inputs; the Docker build verifies pinned archive digests, but this
does not make every runtime use safe. The findings remain real if a tool follows
attacker-controlled URLs or handles untrusted responses. They are temporarily accepted
only for these exact fingerprints while Debian's Trixie fix is pending. Review the
[Debian curl tracker](https://security-tracker.debian.org/tracker/source-package/curl),
[CVE-2026-12064](https://security-tracker.debian.org/tracker/CVE-2026-12064), and
[CVE-2026-8458](https://security-tracker.debian.org/tracker/CVE-2026-8458) again before
the 2026-11-02 expiry.

## OpenSSH client

The image contains `openssh-client 1:10.0p1-7+deb13u4`, not `sshd`. Newly reported
`CVE-2026-106553` and `CVE-2026-106555` affect the server and are not reachable through
the installed client package. `CVE-2026-106583` affects the client: a malicious username
can cause command injection. Do not pass untrusted usernames to ssh. The older accepted
findings `CVE-2026-59999`, `CVE-2026-60000`, and `CVE-2026-60002` remain in the exact
baseline; the first two are server-side and the last is a client use-after-free
requiring a peer to change its host key during re-key. Debian has no fixed Trixie
package at review time. This is a temporary acceptance of a real client risk, not a
claim of safety. See the
[Debian tracker for CVE-2026-106553](https://security-tracker.debian.org/tracker/CVE-2026-106553),
[CVE-2026-106555](https://security-tracker.debian.org/tracker/CVE-2026-106555), and
[CVE-2026-106583](https://security-tracker.debian.org/tracker/CVE-2026-106583).

## Prebuilt Go tools and Dolt compression

`CVE-2026-111943` appears in 11 prebuilt binaries: Docker (`docker`, `docker-proxy`,
`dockerd`, Go 1.26.9); RootlessKit (`rootlessctl`, `rootlesskit`,
`rootlesskit-docker-proxy`, Go 1.26.9 in v3.3.0; prior pins used Go 1.26.8); Buildx (Go
1.26.8); `bd` (Go 1.26.7); Syft (Go 1.26.8); and Task (Go 1.27.1). The Go fix is 1.26.10
or 1.27.3. Go generally uses the system trust store, but its new denylist is extra
protection for narrower cases where trust data has not been refreshed. A client that
accepts a chain through one of the compromised intermediate certificates could permit a
man-in-the-middle attack. The exact binary versions remain temporarily accepted until
their publishers ship patched builds; refresh the system trust store and update the pins
promptly. See the [Go CVE record](https://www.cve.org/CVERecord?id=CVE-2026-111943),
[Go vulnerability entry](https://pkg.go.dev/vuln/GO-2026-6809), and
[Go security announcement](https://groups.google.com/g/golang-announce/c/66pojueaQek).

Dolt also reports `CVE-2026-63209` in `github.com/klauspost/compress v1.18.0`, fixed in
v1.18.7. The advisory describes an integer overflow in S2 dictionary encoding that can
cause an out-of-bounds write and process crash when crafted dictionaries reach the
vulnerable path. Dolt 2.6.1, the latest upstream release checked on 2026-10-27, still
declares compress v1.18.0 and therefore does not fix this finding. The exact fingerprint
is temporarily accepted with the Go-tool findings only until 2026-11-02; do not process
untrusted Dolt databases or expose the local server to untrusted clients while it
remains. Replace this acceptance with a fixed Dolt release or verified patched build as
soon as available. See the
[upstream advisory](https://github.com/advisories/GHSA-259r-337f-4rfw),
[fixed release](https://github.com/klauspost/compress/releases/tag/v1.18.7), and
[Dolt 2.6.1 module file](https://github.com/dolthub/dolt/blob/v2.6.1/go/go.mod).

## Disposition

Only the 83 previously unreviewed exact fingerprints were added to
`scripts/image-scan-baseline.tsv`; old accepted entries were not broadened or replaced.
The `debian_pending`, `kernel_headers`, `openssh_pending`, and `go_binaries` groups now
expire on 2026-11-02. Owner `cos-nxu6` must remove rows as fixes ship and recheck all
temporary acceptances before expiry. `cos-dam4` must separately update the host-kernel
assessment. The group expiry is a short review deadline, not a claim that the unresolved
software issues are fixed. The scan also found a LOW setuptools advisory in Black's
isolated environment; the Dockerfile now upgrades that environment to setuptools 84.1.0
or later, where the advisory is fixed.
