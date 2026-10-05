# PR 499: review of all 64 unreviewed image fingerprints

Prepared 5 October 2026. This is an advisory report, not an approved security exception
or implementation change.

## Evidence and scope

The reviewed snapshot is commit `2853fdd578adcc318dae5c2c954b4c6f21b70b93`, from the
[failed DevContainer validation](https://github.com/ff-fab/cosalette/actions/runs/37234924845/job/111532017236).
The scan completed on 4 October 2026 at 23:16 CEST and reported **287 HIGH/CRITICAL
fingerprints, 64 unreviewed fingerprints, and zero secrets**. The 64 entries represent
**59 distinct advisory IDs**, not 64 independent defects. The scan severity is
preserved; it does not by itself establish exploitability.

| Group                                            | Fingerprints | Distinct advisory IDs | Recommended handling                                                                                                               |
| ------------------------------------------------ | -----------: | --------------------: | ---------------------------------------------------------------------------------------------------------------------------------- |
| Linux support headers, linux-libc-dev 6.12.111-1 |           51 |                    51 | Verify header-only image attribution; handle executable kernel risk on the host; review an exact disposition if headers are needed |
| Python package copies                            |            7 |                     7 | Locate and upgrade/remove every owning installation; evaluate stripped vendored code separately                                    |
| Debian Python 3.13 packages                      |            4 |                     1 | One distro interpreter fix or validated TLS-call mitigation                                                                        |
| Debian python3-urllib3                           |            2 |                     2 | Distro backport or replace/remove Debian HTTP consumers                                                                            |
| **Total**                                        |       **64** |                **59** | **51 header findings plus 13 executable-package findings**                                                                         |

**Corrections to the earlier review:** no Go-tool fingerprints occur in this unreviewed
list. Go tools have findings elsewhere in the full scan inventory, but they are outside
these 64. The plaintext log does not include package paths; attributing all seven Python
findings to pip was premature. The repository policy identifies pip-vendored
setuptools/msgpack, and upstream pip's
[vendor manifest](https://github.com/pypa/pip/blob/26.2.1/src/pip/_vendor/vendor.txt)
can help investigate provenance, but the built image still needs path-level
confirmation. Virtualenv may be a separate pipx/tool environment.

The built image's global uv install did install patched versions, but older copies were
still found. Updating a package in one interpreter does not update isolated tool
environments, distro packages, archived seed wheels, or vendored modules. The presence
of the old version alone is insufficient to conclude which executable call path uses it.

## Recommended decision

Fix the 13 executable-package fingerprints first through three work streams: Python
installation ownership (P), Debian Python/HTTP dependencies (D), and separately document
the 51 header attributions (H). Keep the PR open until a fresh build and scan verify the
proposed dispositions. Do not bulk-accept all 64. A narrow exception should follow
evidence about the specific installed code and consumers, with an owner, expiry, and
concrete exit condition.

### P: find and fix the actual Python installations

Obtain a sanitized vulnerability-only Trivy JSON inventory carrying package
paths/identifiers, layer information and locations when available. Preserve secret
findings separately without their match values. Trace each old package to its owning
pip, pipx venv, system package, seed wheel or test fixture. Compare the invoked
executable and interpreter to those locations.

Upgrade the owner rather than only the top-level dependency. For virtualenv, refresh
every affected tool environment and its generated activation scripts/seed caches. For
pip-vendored components, prefer a patched pip release or remove an unused pip
installation if uv and required tools demonstrably work without it. Do not independently
overwrite a vendor namespace or remove dist-info to hide a scanner hit. If pip ships
only a subset that excludes the vulnerable setuptools API, collect file/function
evidence for that advisory specifically.

The table's patch versions are minimums for this snapshot.
[Additional virtualenv activation advisories](https://github.com/pypa/virtualenv/security/advisories)
were published on 1–2 October; select and verify a release covering those as well. After
rebuilding, prove each old path is absent, upgraded, or contains no relevant vulnerable
code; test the remaining package-install and pre-commit flows.

### D: patch or reduce the Debian Python stack

The Dockerfile installs Debian httpie, which pulls in python3-urllib3 and other system
Python packages. Evaluate replacing it with an isolated uv-managed HTTPie installation
whose dependency resolution selects patched urllib3. Simulate any apt removal and
inspect reverse dependencies before removing distro packages; broad autoremove
previously threatened ICU required by bd. Do not overwrite Debian-owned Python modules
with pip.

For Python 3.13, Debian's
[advisory](https://security-tracker.debian.org/tracker/CVE-2026-19553) currently lists
the scanned trixie version as vulnerable, with no fixed trixie version in the downloaded
tracker data. Passing a valid hostname mitigates the documented defect, but that claim
requires checking every relevant system-Python TLS consumer. Verify both TLS hostname
mismatch rejection and valid-host connection behavior. A standalone newer Python
installed beside the distro interpreter does not repair these four package records.

For distro urllib3, the upstream patched version is 2.8.0; a distro fix may use a lower
upstream version with a verified backport. Trivy's empty fixed-version field means no
distro fix was listed in the scan, not that upstream has no fix. Check signed repository
candidates and Debian changelogs during implementation.

### H: separate header attribution from host-kernel exposure

[Debian describes linux-libc-dev](https://packages.debian.org/trixie/linux-libc-dev) as
userspace development headers installed under /usr/include. These headers do not provide
the executing DRM, SMB, filesystem, networking, GPU, or KVM implementations named by the
advisories. Verify the actual package file list and whether the image carries any
executable kernel/module packages before proposing an image-level “vulnerable code not
present” disposition. Do not assume the host is unaffected.

The devcontainer configuration has **privileged: true** for Docker-in-Docker.
[Docker documents privileged containers](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities)
as having expanded capabilities/device access. Identify the actual host kernel build and
vendor backports, loaded modules, architecture and device/network exposure. Host
remediation must patch or disable each relevant affected subsystem; changing image
header versions cannot patch the host. Consider a separate design change toward
restricted Docker-in-Docker/rootless operation or a reviewed remote builder. A mounted
Docker socket is not automatically a safer replacement because it grants control of its
daemon.

Removing linux-libc-dev could break C/native extension and Rust toolchain builds. Retain
needed headers and document the attribution evidence; remove them only after dependency
simulation and meaningful native-build validation. The existing header baseline uses
6.12.107-1, whereas the scan found 6.12.111-1. **51 of these 51 advisory/package pairs
already exist in the baseline at another installed version.** This explains fingerprint
drift; it does not prove any CVE fixed or authorize a baseline update.

A proposed H disposition should name only the exact image fingerprints verified to be
header attribution, cite the host assessment, retain scanner inventory, assign cos-nxu6
as the existing policy owner subject to confirmation, and keep or shorten the current 27
October expiry. Kernel patching remains an independent host responsibility.

## All fingerprints and recommendations

Numbers below follow the original scanner order. HIGH/CRITICAL is the scanner severity.
“No fix listed” is the scan's distro field; see the linked advisory for current status.
The adjacent TSV preserves the complete six-field fingerprints exactly.

### Executable-package fingerprints (13 rows)

|   # | Target | Advisory                                                                                      | Package / installed                      | Scan severity | Fixed version in scan | Plan |
| --: | ------ | --------------------------------------------------------------------------------------------- | ---------------------------------------- | ------------- | --------------------- | ---- |
|   1 | Python | [CVE-2025-47273](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf)  | setuptools / 70.3.0                      | HIGH          | 78.1.1                | P    |
|   2 | Python | [CVE-2026-102925](https://github.com/pypa/virtualenv/security/advisories/GHSA-p58f-9548-mpm2) | virtualenv / 21.7.9                      | HIGH          | 21.7.13               | P    |
|   3 | Python | [CVE-2026-102930](https://github.com/pypa/virtualenv/security/advisories/GHSA-94p9-xgh2-xp45) | virtualenv / 21.7.9                      | HIGH          | 21.7.12               | P    |
|   4 | Python | [CVE-2026-102937](https://github.com/pypa/virtualenv/security/advisories/GHSA-x78j-v8h9-3j2q) | virtualenv / 21.7.9                      | HIGH          | 21.7.12               | P    |
|   5 | Python | [CVE-2026-97687](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77)  | urllib3 / 2.7.0                          | HIGH          | 2.8.0                 | P    |
|   6 | Python | [CVE-2026-97689](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw)  | urllib3 / 2.7.0                          | HIGH          | 2.8.0                 | P    |
|   7 | Python | [GHSA-6v7p-g79w-8964](https://github.com/advisories/GHSA-6v7p-g79w-8964)                      | msgpack / 1.1.2                          | HIGH          | 1.2.1                 | P    |
|  27 | debian | [CVE-2026-19553](https://security-tracker.debian.org/tracker/CVE-2026-19553)                  | libpython3.13-minimal / 3.13.5-2+deb13u5 | HIGH          | No fix listed         | D    |
|  28 | debian | [CVE-2026-19553](https://security-tracker.debian.org/tracker/CVE-2026-19553)                  | libpython3.13-stdlib / 3.13.5-2+deb13u5  | HIGH          | No fix listed         | D    |
|  29 | debian | [CVE-2026-19553](https://security-tracker.debian.org/tracker/CVE-2026-19553)                  | python3.13 / 3.13.5-2+deb13u5            | HIGH          | No fix listed         | D    |
|  30 | debian | [CVE-2026-19553](https://security-tracker.debian.org/tracker/CVE-2026-19553)                  | python3.13-minimal / 3.13.5-2+deb13u5    | HIGH          | No fix listed         | D    |
|  63 | debian | [CVE-2026-97687](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77)  | python3-urllib3 / 2.3.0-3+deb13u2        | HIGH          | No fix listed         | D    |
|  64 | debian | [CVE-2026-97689](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw)  | python3-urllib3 / 2.3.0-3+deb13u2        | HIGH          | No fix listed         | D    |

#### 1. CVE-2025-47273 — setuptools 70.3.0

**Finding:** PackageIndex download filenames can escape the destination directory and
overwrite files.

**Recommendation (P1):** Locate every 70.3.0 copy. Upgrade the owning installation to
setuptools >=78.1.1 or remove an unused installation. If pip vendors only a stripped
subset, establish whether package_index/PackageIndex exists before claiming this
specific vulnerable code is present.

**Interim control and gotcha:** Use trusted indexes and isolated builds. Trust
restrictions reduce exposure but do not repair path handling. Do not delete package
metadata just to hide the finding.

**Verification:** Identify the detected file path and owning environment; verify
replacement/removal, then exercise the affected package workflow. Rescan the final image
and check this precise fingerprint.
[Primary advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf).

#### 2. CVE-2026-102925 — virtualenv 21.7.9

**Finding:** Generated bash/zsh and fish activation scripts can execute shell syntax
embedded in paths.

**Recommendation (P1):** Upgrade or remove the actual virtualenv 21.7.9 environment; the
snapshot minimum is 21.7.13. Locate pipx environments and preinstalled tool venvs as
well as the global interpreter. Regenerate any activation scripts created by the
vulnerable version.

**Interim control and gotcha:** Use uv for project environments where supported; do not
source untrusted or relocated activation scripts. Quoting mistakes make data become
shell code; avoid interpolating untrusted paths into generated scripts.

**Verification:** Identify the detected file path and owning environment; verify
replacement/removal, then exercise the affected package workflow. Rescan the final image
and check this precise fingerprint.
[Primary advisory](https://github.com/pypa/virtualenv/security/advisories/GHSA-p58f-9548-mpm2).

#### 3. CVE-2026-102930 — virtualenv 21.7.9

**Finding:** Downloaded seed wheels lack an independent integrity check before caching
and installation.

**Recommendation (P1 with applicability review):** Upgrade the owning virtualenv
installation to >=21.7.12, preferably a current release covering all activation
advisories. Recreate seed caches and dependent environments after the upgrade.

**Interim control and gotcha:** Avoid --download and periodic seed-wheel updates while
unpatched; use verified local seeds. The upstream fix skips its PyPI digest comparison
when a custom index is configured, so a private index still needs its own integrity
controls. Upstream rates this Low; keep Trivy HIGH recorded separately.

**Verification:** Identify the detected file path and owning environment; verify
replacement/removal, then exercise the affected package workflow. Rescan the final image
and check this precise fingerprint.
[Primary advisory](https://github.com/pypa/virtualenv/security/advisories/GHSA-94p9-xgh2-xp45).

#### 4. CVE-2026-102937 — virtualenv 21.7.9

**Finding:** Unescaped virtualenv prompts can inject commands into Windows activate.bat.

**Recommendation (P2 applicability; bundle upgrade with P1):** Upgrade virtualenv
to >=21.7.12 or remove the unused old installation. This exact Windows batch execution
path does not run in the Linux image; document that platform limit if the package must
temporarily remain.

**Interim control and gotcha:** Check whether environments/scripts are generated for
Windows consumers or distributed outside the container. Linux-only execution evidence
can support a narrowly scoped applicability disposition; it does not dispose of the
separate bash/fish advisory.

**Verification:** Identify the detected file path and owning environment; verify
replacement/removal, then exercise the affected package workflow. Rescan the final image
and check this precise fingerprint.
[Primary advisory](https://github.com/pypa/virtualenv/security/advisories/GHSA-x78j-v8h9-3j2q).

#### 5. CVE-2026-97687 — urllib3 2.7.0

**Finding:** HTTPS proxy TLS configuration can be confused with target-server TLS
configuration, weakening proxy validation or disclosing client identity.

**Recommendation (P1):** For Python 2.7.0 copies, upgrade the owner to urllib3 >=2.8.0.
For Debian python3-urllib3 2.3.0-3+deb13u2, use a verified Debian backport/security
update or remove the Debian consumer stack if unused; an install into /usr/local Python
does not patch /usr/lib/python3/dist-packages.

**Interim control and gotcha:** Audit HTTPS proxy use, proxy_ssl_context and target
client certificates. Require proxy certificate validation and separate proxy/target TLS
settings. Normal target TLS in CONNECT mode remains a separate boundary; this is not a
universal HTTPS bypass.

**Verification:** Identify the detected file path and owning environment; verify
replacement/removal, then exercise the affected package workflow. Rescan the final image
and check this precise fingerprint.
[Primary advisory](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77).

#### 6. CVE-2026-97689 — urllib3 2.7.0

**Finding:** A malicious chunked HTTP response can make the streaming parser buffer an
unbounded chunk-size line, exhausting memory.

**Recommendation (P1):** For Python 2.7.0 copies, upgrade the owner to urllib3 >=2.8.0.
For Debian python3-urllib3, obtain a verified distro fix or replace/remove its consumer
stack. Enforce resource limits while any vulnerable HTTP consumer remains.

**Interim control and gotcha:** Avoid streaming chunked responses from untrusted servers
until patched. A read timeout does not bound an attacker who continues sending bytes,
and buffering the entire response is not a safe general solution for large bodies.

**Verification:** Identify the detected file path and owning environment; verify
replacement/removal, then exercise the affected package workflow. Rescan the final image
and check this precise fingerprint.
[Primary advisory](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw).

#### 7. GHSA-6v7p-g79w-8964 — msgpack 1.1.2

**Finding:** Reusing a MessagePack Unpacker after a caught parser error can produce an
out-of-bounds read or crash.

**Recommendation (P1):** Locate the 1.1.2 copy and upgrade its owning distribution to
msgpack >=1.2.1, or remove the unused owner. A new top-level msgpack package will not
replace pip/CacheControl vendored copies.

**Interim control and gotcha:** Discard an Unpacker after any parsing error and avoid
processing untrusted cache/data files with the old copy. Verify whether the relevant C
extension and reuse path exist in the detected bundled subset before declaring direct
exposure.

**Verification:** Identify the detected file path and owning environment; verify
replacement/removal, then exercise the affected package workflow. Rescan the final image
and check this precise fingerprint.
[Primary advisory](https://github.com/advisories/GHSA-6v7p-g79w-8964).

#### 27. CVE-2026-19553 — libpython3.13-minimal 3.13.5-2+deb13u5

**Finding:** Python SSLObject/asyncio can silently skip hostname checking when a valid
server_hostname is omitted.

**Recommendation (P1):** Treat all four Debian binary-package rows as one python3.13
source-package remediation. Install a verified Debian security backport when available.
Alternatively remove Python 3.13 and dependent distro utilities only after checking
reverse dependencies and tooling compatibility.

**Interim control and gotcha:** Require a nonempty, non-None server_hostname on
SSLContext.wrap_bio and affected asyncio TLS connection/start_tls calls; retain
certificate verification. Audit consumers of the Debian interpreter. The project Python
3.14 interpreter is separate and also needs its own version check.

**Verification:** Check the distro package ownership and consumers; verify the
backported fix or removal and exercise remaining TLS/HTTP tooling. Rescan the final
image and check this precise fingerprint.
[Primary advisory](https://security-tracker.debian.org/tracker/CVE-2026-19553).

#### 28. CVE-2026-19553 — libpython3.13-stdlib 3.13.5-2+deb13u5

**Finding:** Python SSLObject/asyncio can silently skip hostname checking when a valid
server_hostname is omitted.

**Recommendation (P1):** Treat all four Debian binary-package rows as one python3.13
source-package remediation. Install a verified Debian security backport when available.
Alternatively remove Python 3.13 and dependent distro utilities only after checking
reverse dependencies and tooling compatibility.

**Interim control and gotcha:** Require a nonempty, non-None server_hostname on
SSLContext.wrap_bio and affected asyncio TLS connection/start_tls calls; retain
certificate verification. Audit consumers of the Debian interpreter. The project Python
3.14 interpreter is separate and also needs its own version check.

**Verification:** Check the distro package ownership and consumers; verify the
backported fix or removal and exercise remaining TLS/HTTP tooling. Rescan the final
image and check this precise fingerprint.
[Primary advisory](https://security-tracker.debian.org/tracker/CVE-2026-19553).

#### 29. CVE-2026-19553 — python3.13 3.13.5-2+deb13u5

**Finding:** Python SSLObject/asyncio can silently skip hostname checking when a valid
server_hostname is omitted.

**Recommendation (P1):** Treat all four Debian binary-package rows as one python3.13
source-package remediation. Install a verified Debian security backport when available.
Alternatively remove Python 3.13 and dependent distro utilities only after checking
reverse dependencies and tooling compatibility.

**Interim control and gotcha:** Require a nonempty, non-None server_hostname on
SSLContext.wrap_bio and affected asyncio TLS connection/start_tls calls; retain
certificate verification. Audit consumers of the Debian interpreter. The project Python
3.14 interpreter is separate and also needs its own version check.

**Verification:** Check the distro package ownership and consumers; verify the
backported fix or removal and exercise remaining TLS/HTTP tooling. Rescan the final
image and check this precise fingerprint.
[Primary advisory](https://security-tracker.debian.org/tracker/CVE-2026-19553).

#### 30. CVE-2026-19553 — python3.13-minimal 3.13.5-2+deb13u5

**Finding:** Python SSLObject/asyncio can silently skip hostname checking when a valid
server_hostname is omitted.

**Recommendation (P1):** Treat all four Debian binary-package rows as one python3.13
source-package remediation. Install a verified Debian security backport when available.
Alternatively remove Python 3.13 and dependent distro utilities only after checking
reverse dependencies and tooling compatibility.

**Interim control and gotcha:** Require a nonempty, non-None server_hostname on
SSLContext.wrap_bio and affected asyncio TLS connection/start_tls calls; retain
certificate verification. Audit consumers of the Debian interpreter. The project Python
3.14 interpreter is separate and also needs its own version check.

**Verification:** Check the distro package ownership and consumers; verify the
backported fix or removal and exercise remaining TLS/HTTP tooling. Rescan the final
image and check this precise fingerprint.
[Primary advisory](https://security-tracker.debian.org/tracker/CVE-2026-19553).

#### 63. CVE-2026-97687 — python3-urllib3 2.3.0-3+deb13u2

**Finding:** HTTPS proxy TLS configuration can be confused with target-server TLS
configuration, weakening proxy validation or disclosing client identity.

**Recommendation (P1):** For Python 2.7.0 copies, upgrade the owner to urllib3 >=2.8.0.
For Debian python3-urllib3 2.3.0-3+deb13u2, use a verified Debian backport/security
update or remove the Debian consumer stack if unused; an install into /usr/local Python
does not patch /usr/lib/python3/dist-packages.

**Interim control and gotcha:** Audit HTTPS proxy use, proxy_ssl_context and target
client certificates. Require proxy certificate validation and separate proxy/target TLS
settings. Normal target TLS in CONNECT mode remains a separate boundary; this is not a
universal HTTPS bypass.

**Verification:** Check the distro package ownership and consumers; verify the
backported fix or removal and exercise remaining TLS/HTTP tooling. Rescan the final
image and check this precise fingerprint.
[Primary advisory](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77).

#### 64. CVE-2026-97689 — python3-urllib3 2.3.0-3+deb13u2

**Finding:** A malicious chunked HTTP response can make the streaming parser buffer an
unbounded chunk-size line, exhausting memory.

**Recommendation (P1):** For Python 2.7.0 copies, upgrade the owner to urllib3 >=2.8.0.
For Debian python3-urllib3, obtain a verified distro fix or replace/remove its consumer
stack. Enforce resource limits while any vulnerable HTTP consumer remains.

**Interim control and gotcha:** Avoid streaming chunked responses from untrusted servers
until patched. A read timeout does not bound an attacker who continues sending bytes,
and buffering the entire response is not a safe general solution for large bodies.

**Verification:** Check the distro package ownership and consumers; verify the
backported fix or removal and exercise remaining TLS/HTTP tooling. Rescan the final
image and check this precise fingerprint.
[Primary advisory](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw).

### Kernel-header fingerprints (51 rows)

Every row below has target **debian**, package **linux-libc-dev**, installed version
**6.12.111-1**, and fixed version **no fix listed**. These shared fields plus each
ID/severity identify the complete fingerprint. For every row the image recommendation is
**H**: verify that only userspace headers supply the attribution, retain/remove headers
according to native-build requirements, and propose an exact documented disposition only
after that proof. The row's description identifies the specific host subsystem to review
and patch if present. Debian's current tracker data marks these Linux source advisories
open for trixie; it does not establish whether the separate host kernel is vulnerable.

|   # | Advisory                                                                     | Scan severity | Underlying kernel finding / host check                                 | Recommendation                               |
| --: | ---------------------------------------------------------------------------- | ------------- | ---------------------------------------------------------------------- | -------------------------------------------- |
|   8 | [CVE-2026-43185](https://security-tracker.debian.org/tracker/CVE-2026-43185) | CRITICAL      | ksmbd SMB Direct negotiation mishandles signed transfer sizes.         | H; assess and patch the named host subsystem |
|   9 | [CVE-2013-7445](https://security-tracker.debian.org/tracker/CVE-2013-7445)   | HIGH          | DRM/GEM graphics allocations can exhaust memory.                       | H; assess and patch the named host subsystem |
|  10 | [CVE-2019-19449](https://security-tracker.debian.org/tracker/CVE-2019-19449) | HIGH          | A crafted F2FS filesystem can cause an out-of-bounds read.             | H; assess and patch the named host subsystem |
|  11 | [CVE-2019-19814](https://security-tracker.debian.org/tracker/CVE-2019-19814) | HIGH          | A crafted F2FS filesystem can cause an out-of-bounds write.            | H; assess and patch the named host subsystem |
|  12 | [CVE-2021-3847](https://security-tracker.debian.org/tracker/CVE-2021-3847)   | HIGH          | OverlayFS copy-up can bypass nosuid expectations.                      | H; assess and patch the named host subsystem |
|  13 | [CVE-2021-3864](https://security-tracker.debian.org/tracker/CVE-2021-3864)   | HIGH          | SUID descendant core dumps can create privileged files.                | H; assess and patch the named host subsystem |
|  14 | [CVE-2024-21803](https://security-tracker.debian.org/tracker/CVE-2024-21803) | HIGH          | Bluetooth socket lifetime error can cause use-after-free.              | H; assess and patch the named host subsystem |
|  15 | [CVE-2024-58015](https://security-tracker.debian.org/tracker/CVE-2024-58015) | HIGH          | ath12k Wi-Fi statistics can read outside a buffer.                     | H; assess and patch the named host subsystem |
|  16 | [CVE-2025-38137](https://security-tracker.debian.org/tracker/CVE-2025-38137) | HIGH          | PCI power-control rescan work can outlive freed state.                 | H; assess and patch the named host subsystem |
|  17 | [CVE-2025-38187](https://security-tracker.debian.org/tracker/CVE-2025-38187) | HIGH          | Nouveau GPU RPC fragmentation can reuse freed memory.                  | H; assess and patch the named host subsystem |
|  18 | [CVE-2025-38204](https://security-tracker.debian.org/tracker/CVE-2025-38204) | HIGH          | JFS directory indexing can read outside an array.                      | H; assess and patch the named host subsystem |
|  19 | [CVE-2025-38421](https://security-tracker.debian.org/tracker/CVE-2025-38421) | HIGH          | AMD platform-management cleanup can free memory twice.                 | H; assess and patch the named host subsystem |
|  20 | [CVE-2025-38636](https://security-tracker.debian.org/tracker/CVE-2025-38636) | HIGH          | Runtime-verification tracepoints read beyond string storage.           | H; assess and patch the named host subsystem |
|  21 | [CVE-2025-39859](https://security-tracker.debian.org/tracker/CVE-2025-39859) | HIGH          | OCP precision-clock watchdog teardown can use freed memory.            | H; assess and patch the named host subsystem |
|  22 | [CVE-2025-39862](https://security-tracker.debian.org/tracker/CVE-2025-39862) | HIGH          | MediaTek Wi-Fi restart can corrupt station lists.                      | H; assess and patch the named host subsystem |
|  23 | [CVE-2025-39958](https://security-tracker.debian.org/tracker/CVE-2025-39958) | HIGH          | s390 IOMMU teardown mishandles surprise device removal.                | H; assess and patch the named host subsystem |
|  24 | [CVE-2025-40025](https://security-tracker.debian.org/tracker/CVE-2025-40025) | HIGH          | Corrupt F2FS node metadata can trigger a kernel panic.                 | H; assess and patch the named host subsystem |
|  25 | [CVE-2025-68174](https://security-tracker.debian.org/tracker/CVE-2025-68174) | HIGH          | AMD GPU compute partition teardown races process cleanup.              | H; assess and patch the named host subsystem |
|  26 | [CVE-2025-68735](https://security-tracker.debian.org/tracker/CVE-2025-68735) | HIGH          | Panthor GPU group creation can use freed memory.                       | H; assess and patch the named host subsystem |
|  31 | [CVE-2026-23102](https://security-tracker.debian.org/tracker/CVE-2026-23102) | HIGH          | ARM64 signal handling incorrectly restores SVE state.                  | H; assess and patch the named host subsystem |
|  32 | [CVE-2026-23208](https://security-tracker.debian.org/tracker/CVE-2026-23208) | HIGH          | USB audio accepts excessive frame counts.                              | H; assess and patch the named host subsystem |
|  33 | [CVE-2026-23327](https://security-tracker.debian.org/tracker/CVE-2026-23327) | HIGH          | CXL mailbox access lacks sufficient payload bounds checks.             | H; assess and patch the named host subsystem |
|  34 | [CVE-2026-31493](https://security-tracker.debian.org/tracker/CVE-2026-31493) | HIGH          | EFA RDMA completion processing can use freed context.                  | H; assess and patch the named host subsystem |
|  35 | [CVE-2026-31536](https://security-tracker.debian.org/tracker/CVE-2026-31536) | HIGH          | SMB Direct completion handling mishandles unsignalled sends.           | H; assess and patch the named host subsystem |
|  36 | [CVE-2026-31568](https://security-tracker.debian.org/tracker/CVE-2026-31568) | HIGH          | s390 donated secure memory lacks required access fixups.               | H; assess and patch the named host subsystem |
|  37 | [CVE-2026-43263](https://security-tracker.debian.org/tracker/CVE-2026-43263) | HIGH          | Wave5 video-codec processing can dereference a null pointer.           | H; assess and patch the named host subsystem |
|  38 | [CVE-2026-46130](https://security-tracker.debian.org/tracker/CVE-2026-46130) | HIGH          | dm-verity FEC mishandles parity reads across block boundaries.         | H; assess and patch the named host subsystem |
|  39 | [CVE-2026-46181](https://security-tracker.debian.org/tracker/CVE-2026-46181) | HIGH          | mlx4 RDMA event processing misuses RCU lifetime protection.            | H; assess and patch the named host subsystem |
|  40 | [CVE-2026-46279](https://security-tracker.debian.org/tracker/CVE-2026-46279) | HIGH          | Page allocation tags can reference stale code-tag state.               | H; assess and patch the named host subsystem |
|  41 | [CVE-2026-52991](https://security-tracker.debian.org/tracker/CVE-2026-52991) | HIGH          | Pressure-stall writes race file release.                               | H; assess and patch the named host subsystem |
|  42 | [CVE-2026-53000](https://security-tracker.debian.org/tracker/CVE-2026-53000) | HIGH          | Netfilter NAT operations are released without suitable RCU protection. | H; assess and patch the named host subsystem |
|  43 | [CVE-2026-53091](https://security-tracker.debian.org/tracker/CVE-2026-53091) | HIGH          | Network queue length accounting reads insufficiently pulled headers.   | H; assess and patch the named host subsystem |
|  44 | [CVE-2026-53109](https://security-tracker.debian.org/tracker/CVE-2026-53109) | HIGH          | PowerPC page-table fragment cleanup can corrupt page state.            | H; assess and patch the named host subsystem |
|  45 | [CVE-2026-53118](https://security-tracker.debian.org/tracker/CVE-2026-53118) | HIGH          | vDPA driver override handling needs the generic safe implementation.   | H; assess and patch the named host subsystem |
|  46 | [CVE-2026-53277](https://security-tracker.debian.org/tracker/CVE-2026-53277) | HIGH          | ARM64 KVM page-table walks lack SRCU protection.                       | H; assess and patch the named host subsystem |
|  47 | [CVE-2026-53330](https://security-tracker.debian.org/tracker/CVE-2026-53330) | HIGH          | AMD display link-training timing reads beyond available data.          | H; assess and patch the named host subsystem |
|  48 | [CVE-2026-63879](https://security-tracker.debian.org/tracker/CVE-2026-63879) | HIGH          | AMDGPU heterogeneous-memory page handling needs correction.            | H; assess and patch the named host subsystem |
|  49 | [CVE-2026-64283](https://security-tracker.debian.org/tracker/CVE-2026-64283) | HIGH          | KVM guest-memory offsets and sizes use unsafe signed arithmetic.       | H; assess and patch the named host subsystem |
|  50 | [CVE-2026-68409](https://security-tracker.debian.org/tracker/CVE-2026-68409) | HIGH          | Wi-Fi link receive statistics are freed before RCU readers finish.     | H; assess and patch the named host subsystem |
|  51 | [CVE-2026-68426](https://security-tracker.debian.org/tracker/CVE-2026-68426) | HIGH          | IPsec async crypto leaves a stale packet-list pointer.                 | H; assess and patch the named host subsystem |
|  52 | [CVE-2026-68470](https://security-tracker.debian.org/tracker/CVE-2026-68470) | HIGH          | Wi-Fi extension-frame parsing lacks layout validation.                 | H; assess and patch the named host subsystem |
|  53 | [CVE-2026-72042](https://security-tracker.debian.org/tracker/CVE-2026-72042) | HIGH          | IPMI event delivery can underflow a user reference count.              | H; assess and patch the named host subsystem |
|  54 | [CVE-2026-72098](https://security-tracker.debian.org/tracker/CVE-2026-72098) | HIGH          | dm-verity FEC calculation can overflow a buffer.                       | H; assess and patch the named host subsystem |
|  55 | [CVE-2026-72463](https://security-tracker.debian.org/tracker/CVE-2026-72463) | HIGH          | IPsec async resumption can access a freed device.                      | H; assess and patch the named host subsystem |
|  56 | [CVE-2026-74269](https://security-tracker.debian.org/tracker/CVE-2026-74269) | HIGH          | Broadcom XDP packet head growth can underflow the receive head.        | H; assess and patch the named host subsystem |
|  57 | [CVE-2026-74520](https://security-tracker.debian.org/tracker/CVE-2026-74520) | HIGH          | IOMMU page-fault group ownership can cause use-after-free.             | H; assess and patch the named host subsystem |
|  58 | [CVE-2026-74752](https://security-tracker.debian.org/tracker/CVE-2026-74752) | HIGH          | SCTP cookies carry unchecked authentication lengths and identifiers.   | H; assess and patch the named host subsystem |
|  59 | [CVE-2026-89631](https://security-tracker.debian.org/tracker/CVE-2026-89631) | HIGH          | SMB client tree-connect parsing can read beyond the response.          | H; assess and patch the named host subsystem |
|  60 | [CVE-2026-89633](https://security-tracker.debian.org/tracker/CVE-2026-89633) | HIGH          | SMB client transaction offsets permit out-of-bounds reads/writes.      | H; assess and patch the named host subsystem |
|  61 | [CVE-2026-89638](https://security-tracker.debian.org/tracker/CVE-2026-89638) | HIGH          | SMB writes can preserve setuid/setgid bits unexpectedly.               | H; assess and patch the named host subsystem |
|  62 | [CVE-2026-89675](https://security-tracker.debian.org/tracker/CVE-2026-89675) | HIGH          | NFS server asynchronous-copy cancellation races object teardown.       | H; assess and patch the named host subsystem |

## Implementation order and acceptance conditions

1. Capture the complete package-location evidence from a new build of the PR head;
   classify live installations, vendor subsets, seed archives and fixtures. This is
   prerequisite to claiming the seven Python findings fixed or unreachable.
2. Repair Python ownership (P): upgrade/remove actual older copies, regenerate affected
   virtualenv scripts/caches, and verify pre-commit and project environment creation. Do
   not rely on the already-added global version constraints alone.
3. Repair distro dependencies (D): replace/remove redundant HTTPie/system urllib3
   consumers where feasible, verify any distro security backport, and audit the Python
   3.13 TLS hostname calls. Treat the four Python rows as one source-package issue.
4. Complete header/host review (H): inspect package files, confirm needed development
   headers, record the host kernel/modules and vendor patch state, and review the
   privileged-container configuration. Only then prepare narrow exception text if
   executable kernel code is absent from the image.
5. Review remaining exception proposals individually. Python exceptions need their own
   consumer and code-path evidence; do not place all seven into pip_vendor merely
   because they are Python. The Windows-only virtualenv row may warrant an applicability
   disposition, while its separate Linux activation vulnerability still needs repair. A
   time-limited acceptance is residual risk, not a fix.
6. Run the repository image-policy and container checks on the final changes, including
   task test:security:docker:policy, task security:docker:lint, and task
   security:docker:scan against the rebuilt image. Require zero secrets and zero
   unreviewed fingerprints after approved dispositions; unchanged acknowledged findings
   must still be visible. Run native-build validation if headers or compiler
   dependencies are removed.
7. Merge PR 499 only after the final head passes all relevant checks. Close
   cos-8jxg.1/.2/.3 after merge; keep cos-nxu6 and any outstanding security follow-up
   issues open until their own remediation/evidence is complete. The beads database was
   unavailable during this review, so owner/status confirmation remains necessary.

## Limits and approval boundary

This report reviews the 64 unreviewed fingerprints in the specified snapshot, not every
accepted finding among the full 287. No fresh image was built for this report, and host
inventory and Python package paths were unavailable. Exploitability statements above are
conditional, with concrete evidence required before any exception. Primary advisory
descriptions and Debian source-package status were checked on 5 October 2026.

Automatic approval review previously rejected adding all 64 fingerprints to the
acceptance baseline because it would weaken the gate by accepting unresolved
HIGH/CRITICAL findings. No retry or baseline mutation is part of this report. A reviewed
per-finding proposal should distinguish code absent from the image from executable
vulnerable code accepted temporarily and make the retained risk explicit.

## Individual host exposure checks for the 51 header fingerprints

These are investigation steps, not claims that the host is affected. Each inherits plan
H and the exact header package/version listed above. For every entry, the image decision
requires proof that the scanner detected headers without the vulnerable executable
implementation; the host decision requires its own kernel/version/configuration
evidence.

### Fingerprint 8: CVE-2026-43185 (CRITICAL)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — SMB/NFS server or client operations:** Identify which host
SMB/NFS client/server implementations are enabled, the endpoints they contact or expose,
and whether the affected operation can be triggered. SMB client and server advisories
require different checks.

**Proposed mitigation:** Disable unused services/modules, restrict reachable peers, and
patch the relevant host client/server implementation; do not assume image network
isolation protects a privileged container.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-43185).

### Fingerprint 9: CVE-2013-7445 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — GPU/DRM driver access:** Identify the host GPU driver and whether
its device nodes are exposed to this container. A driver-specific finding requires that
implementation and its affected operation to be available.

**Proposed mitigation:** Restrict GPU device access where unnecessary; patch the host
driver/kernel using a vendor-confirmed fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2013-7445).

### Fingerprint 10: CVE-2019-19449 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — F2FS/JFS filesystem handling:** Check host filesystem support and
whether untrusted disk images or corrupt filesystems can be mounted. Privileged
container mounting permissions materially affect this exposure.

**Proposed mitigation:** Prevent untrusted filesystem mounts; remove unnecessary
mounting capabilities and patch affected host filesystem code.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2019-19449).

### Fingerprint 11: CVE-2019-19814 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — F2FS/JFS filesystem handling:** Check host filesystem support and
whether untrusted disk images or corrupt filesystems can be mounted. Privileged
container mounting permissions materially affect this exposure.

**Proposed mitigation:** Prevent untrusted filesystem mounts; remove unnecessary
mounting capabilities and patch affected host filesystem code.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2019-19814).

### Fingerprint 12: CVE-2021-3847 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2021-3847).

### Fingerprint 13: CVE-2021-3864 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2021-3864).

### Fingerprint 14: CVE-2024-21803 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2024-21803).

### Fingerprint 15: CVE-2024-58015 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — Wi-Fi driver and wireless frame processing:** Check the host
wireless driver, affected hardware, and whether wireless interfaces or operations are
available. Hardware-specific findings do not apply merely because a header is installed.

**Proposed mitigation:** Disable unused wireless drivers/interfaces and patch the host
wireless stack; confirm applicability against the exact driver.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2024-58015).

### Fingerprint 16: CVE-2025-38137 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-38137).

### Fingerprint 17: CVE-2025-38187 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — GPU/DRM driver access:** Identify the host GPU driver and whether
its device nodes are exposed to this container. A driver-specific finding requires that
implementation and its affected operation to be available.

**Proposed mitigation:** Restrict GPU device access where unnecessary; patch the host
driver/kernel using a vendor-confirmed fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-38187).

### Fingerprint 18: CVE-2025-38204 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — F2FS/JFS filesystem handling:** Check host filesystem support and
whether untrusted disk images or corrupt filesystems can be mounted. Privileged
container mounting permissions materially affect this exposure.

**Proposed mitigation:** Prevent untrusted filesystem mounts; remove unnecessary
mounting capabilities and patch affected host filesystem code.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-38204).

### Fingerprint 19: CVE-2025-38421 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-38421).

### Fingerprint 20: CVE-2025-38636 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-38636).

### Fingerprint 21: CVE-2025-39859 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-39859).

### Fingerprint 22: CVE-2025-39862 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — Wi-Fi driver and wireless frame processing:** Check the host
wireless driver, affected hardware, and whether wireless interfaces or operations are
available. Hardware-specific findings do not apply merely because a header is installed.

**Proposed mitigation:** Disable unused wireless drivers/interfaces and patch the host
wireless stack; confirm applicability against the exact driver.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-39862).

### Fingerprint 23: CVE-2025-39958 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-39958).

### Fingerprint 24: CVE-2025-40025 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — F2FS/JFS filesystem handling:** Check host filesystem support and
whether untrusted disk images or corrupt filesystems can be mounted. Privileged
container mounting permissions materially affect this exposure.

**Proposed mitigation:** Prevent untrusted filesystem mounts; remove unnecessary
mounting capabilities and patch affected host filesystem code.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-40025).

### Fingerprint 25: CVE-2025-68174 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — GPU/DRM driver access:** Identify the host GPU driver and whether
its device nodes are exposed to this container. A driver-specific finding requires that
implementation and its affected operation to be available.

**Proposed mitigation:** Restrict GPU device access where unnecessary; patch the host
driver/kernel using a vendor-confirmed fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-68174).

### Fingerprint 26: CVE-2025-68735 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — GPU/DRM driver access:** Identify the host GPU driver and whether
its device nodes are exposed to this container. A driver-specific finding requires that
implementation and its affected operation to be available.

**Proposed mitigation:** Restrict GPU device access where unnecessary; patch the host
driver/kernel using a vendor-confirmed fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2025-68735).

### Fingerprint 31: CVE-2026-23102 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-23102).

### Fingerprint 32: CVE-2026-23208 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-23208).

### Fingerprint 33: CVE-2026-23327 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-23327).

### Fingerprint 34: CVE-2026-31493 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — RDMA device and completion processing:** Identify the host RDMA
driver and device exposure, and assess the affected completion/event path. Driver and
hardware matching is required before determining host applicability.

**Proposed mitigation:** Remove unnecessary RDMA device access and patch the applicable
host driver.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-31493).

### Fingerprint 35: CVE-2026-31536 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — SMB/NFS server or client operations:** Identify which host
SMB/NFS client/server implementations are enabled, the endpoints they contact or expose,
and whether the affected operation can be triggered. SMB client and server advisories
require different checks.

**Proposed mitigation:** Disable unused services/modules, restrict reachable peers, and
patch the relevant host client/server implementation; do not assume image network
isolation protects a privileged container.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-31536).

### Fingerprint 36: CVE-2026-31568 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-31568).

### Fingerprint 37: CVE-2026-43263 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-43263).

### Fingerprint 38: CVE-2026-46130 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — dm-verity forward error correction:** Determine whether dm-verity
with FEC is enabled and whether attacker-controlled or damaged backing data reaches its
parity/error-correction processing.

**Proposed mitigation:** Use trusted verified backing images, restrict device-mapper
operations, and patch the host implementation if FEC is in use.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-46130).

### Fingerprint 39: CVE-2026-46181 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — RDMA device and completion processing:** Identify the host RDMA
driver and device exposure, and assess the affected completion/event path. Driver and
hardware matching is required before determining host applicability.

**Proposed mitigation:** Remove unnecessary RDMA device access and patch the applicable
host driver.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-46181).

### Fingerprint 40: CVE-2026-46279 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-46279).

### Fingerprint 41: CVE-2026-52991 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-52991).

### Fingerprint 42: CVE-2026-53000 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — network packet processing and configuration:** Check the affected
NAT/IPsec/SCTP/XDP driver feature and whether local network configuration or remote
packets reach it. Determine the specific feature and trigger from the linked advisory.

**Proposed mitigation:** Disable unused affected features, restrict network
administration and interfaces, and patch the host networking implementation.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-53000).

### Fingerprint 43: CVE-2026-53091 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — network packet processing and configuration:** Check the affected
NAT/IPsec/SCTP/XDP driver feature and whether local network configuration or remote
packets reach it. Determine the specific feature and trigger from the linked advisory.

**Proposed mitigation:** Disable unused affected features, restrict network
administration and interfaces, and patch the host networking implementation.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-53091).

### Fingerprint 44: CVE-2026-53109 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-53109).

### Fingerprint 45: CVE-2026-53118 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-53118).

### Fingerprint 46: CVE-2026-53277 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — KVM virtualization:** Check the host architecture, KVM modules,
/dev/kvm exposure and affected guest-memory/page-table operations. Architecture and
kernel configuration constrain applicability.

**Proposed mitigation:** Restrict KVM device access and untrusted guest workloads;
install the applicable host kernel fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-53277).

### Fingerprint 47: CVE-2026-53330 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — GPU/DRM driver access:** Identify the host GPU driver and whether
its device nodes are exposed to this container. A driver-specific finding requires that
implementation and its affected operation to be available.

**Proposed mitigation:** Restrict GPU device access where unnecessary; patch the host
driver/kernel using a vendor-confirmed fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-53330).

### Fingerprint 48: CVE-2026-63879 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — GPU/DRM driver access:** Identify the host GPU driver and whether
its device nodes are exposed to this container. A driver-specific finding requires that
implementation and its affected operation to be available.

**Proposed mitigation:** Restrict GPU device access where unnecessary; patch the host
driver/kernel using a vendor-confirmed fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-63879).

### Fingerprint 49: CVE-2026-64283 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — KVM virtualization:** Check the host architecture, KVM modules,
/dev/kvm exposure and affected guest-memory/page-table operations. Architecture and
kernel configuration constrain applicability.

**Proposed mitigation:** Restrict KVM device access and untrusted guest workloads;
install the applicable host kernel fix.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-64283).

### Fingerprint 50: CVE-2026-68409 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — Wi-Fi driver and wireless frame processing:** Check the host
wireless driver, affected hardware, and whether wireless interfaces or operations are
available. Hardware-specific findings do not apply merely because a header is installed.

**Proposed mitigation:** Disable unused wireless drivers/interfaces and patch the host
wireless stack; confirm applicability against the exact driver.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-68409).

### Fingerprint 51: CVE-2026-68426 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — network packet processing and configuration:** Check the affected
NAT/IPsec/SCTP/XDP driver feature and whether local network configuration or remote
packets reach it. Determine the specific feature and trigger from the linked advisory.

**Proposed mitigation:** Disable unused affected features, restrict network
administration and interfaces, and patch the host networking implementation.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-68426).

### Fingerprint 52: CVE-2026-68470 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — Wi-Fi driver and wireless frame processing:** Check the host
wireless driver, affected hardware, and whether wireless interfaces or operations are
available. Hardware-specific findings do not apply merely because a header is installed.

**Proposed mitigation:** Disable unused wireless drivers/interfaces and patch the host
wireless stack; confirm applicability against the exact driver.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-68470).

### Fingerprint 53: CVE-2026-72042 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-72042).

### Fingerprint 54: CVE-2026-72098 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — dm-verity forward error correction:** Determine whether dm-verity
with FEC is enabled and whether attacker-controlled or damaged backing data reaches its
parity/error-correction processing.

**Proposed mitigation:** Use trusted verified backing images, restrict device-mapper
operations, and patch the host implementation if FEC is in use.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-72098).

### Fingerprint 55: CVE-2026-72463 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — network packet processing and configuration:** Check the affected
NAT/IPsec/SCTP/XDP driver feature and whether local network configuration or remote
packets reach it. Determine the specific feature and trigger from the linked advisory.

**Proposed mitigation:** Disable unused affected features, restrict network
administration and interfaces, and patch the host networking implementation.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-72463).

### Fingerprint 56: CVE-2026-74269 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — network packet processing and configuration:** Check the affected
NAT/IPsec/SCTP/XDP driver feature and whether local network configuration or remote
packets reach it. Determine the specific feature and trigger from the linked advisory.

**Proposed mitigation:** Disable unused affected features, restrict network
administration and interfaces, and patch the host networking implementation.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-74269).

### Fingerprint 57: CVE-2026-74520 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation:** Match the specific subsystem, architecture, kernel configuration
and vulnerable operation described in the linked advisory to the actual host. Check
vendor backport evidence rather than comparing version numbers alone. Assess whether
privileged container access makes that operation reachable.

**Proposed mitigation:** Patch the applicable host kernel; until then disable or
restrict the affected operation/subsystem where feasible. Record the control, remaining
exposure and expiry before accepting residual risk.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-74520).

### Fingerprint 58: CVE-2026-74752 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — network packet processing and configuration:** Check the affected
NAT/IPsec/SCTP/XDP driver feature and whether local network configuration or remote
packets reach it. Determine the specific feature and trigger from the linked advisory.

**Proposed mitigation:** Disable unused affected features, restrict network
administration and interfaces, and patch the host networking implementation.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-74752).

### Fingerprint 59: CVE-2026-89631 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — SMB/NFS server or client operations:** Identify which host
SMB/NFS client/server implementations are enabled, the endpoints they contact or expose,
and whether the affected operation can be triggered. SMB client and server advisories
require different checks.

**Proposed mitigation:** Disable unused services/modules, restrict reachable peers, and
patch the relevant host client/server implementation; do not assume image network
isolation protects a privileged container.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-89631).

### Fingerprint 60: CVE-2026-89633 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — SMB/NFS server or client operations:** Identify which host
SMB/NFS client/server implementations are enabled, the endpoints they contact or expose,
and whether the affected operation can be triggered. SMB client and server advisories
require different checks.

**Proposed mitigation:** Disable unused services/modules, restrict reachable peers, and
patch the relevant host client/server implementation; do not assume image network
isolation protects a privileged container.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-89633).

### Fingerprint 61: CVE-2026-89638 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — SMB/NFS server or client operations:** Identify which host
SMB/NFS client/server implementations are enabled, the endpoints they contact or expose,
and whether the affected operation can be triggered. SMB client and server advisories
require different checks.

**Proposed mitigation:** Disable unused services/modules, restrict reachable peers, and
patch the relevant host client/server implementation; do not assume image network
isolation protects a privileged container.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-89638).

### Fingerprint 62: CVE-2026-89675 (HIGH)

**Image proposal:** Confirm `linux-libc-dev 6.12.111-1` owns only development headers
for this attribution. Retain required headers; propose a narrowly documented code-absent
disposition only after inspecting package files and executable kernel/module inventory.
The scan lists no distro fixed version.

**Host investigation — SMB/NFS server or client operations:** Identify which host
SMB/NFS client/server implementations are enabled, the endpoints they contact or expose,
and whether the affected operation can be triggered. SMB client and server advisories
require different checks.

**Proposed mitigation:** Disable unused services/modules, restrict reachable peers, and
patch the relevant host client/server implementation; do not assume image network
isolation protects a privileged container.

**Evidence required to close:** Package file inventory for the image disposition, and a
vendor-fixed host build or documented non-applicability to its configuration.
[Technical advisory](https://security-tracker.debian.org/tracker/CVE-2026-89675).
