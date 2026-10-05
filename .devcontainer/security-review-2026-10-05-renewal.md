# cos-yi5p: renewal of the 11 October acceptance groups

Prepared 5 October 2026 for cos-yi5p. It re-reviews every baseline row in the groups
that expired on 11 October 2026 (`go_binaries`, `libxml2_pending`, `openssh_pending`,
`pip_vendor`). The 27 October groups (`debian_pending`, `kernel_headers`) keep their
expiry. Only their rows that the same scan found stale are removed. The owner of all
groups is still cos-nxu6.

## Evidence and scope

The image was rebuilt from commit `5dfaa038` with
`docker build --pull --no-cache --build-arg INSTALL_CLAUDE_CODE=false .devcontainer`,
using current trixie packages and the unchanged base image digest. It was then scanned
with `task security:docker:scan`. The scan reported **260 HIGH/CRITICAL fingerprints, 0
unreviewed and 0 secrets**. Rows that match no current fingerprint are removed. Every
remaining row matches a current fingerprint exactly, so no row needed new versions. The
new expiry is 2 November 2026, a four-week window. That is longer than the issue's
14-day minimum and matches earlier renewals.

| Group             | Rows before | Removed | Kept | Expiry                   |
| ----------------- | ----------: | ------: | ---: | ------------------------ |
| `go_binaries`     |          47 |      25 |   22 | 2026-10-11 -> 2026-11-02 |
| `libxml2_pending` |          16 |       0 |   16 | 2026-10-11 -> 2026-11-02 |
| `openssh_pending` |           3 |       0 |    3 | 2026-10-11 -> 2026-11-02 |
| `pip_vendor`      |           0 |       - |    - | group deleted            |
| `debian_pending`  |         201 |      33 |  168 | 2026-10-27 (unchanged)   |
| `kernel_headers`  |          90 |      39 |   51 | 2026-10-27 (unchanged)   |

## go_binaries

**Removed (25 rows):** all 11 syft v1.54.0 stdlib rows and all 10 go-task v3.54.0 rows
(stdlib and grpc). The current releases are built with Go 1.26.8 and 1.27.1. The dolt
module rows for golang.org/x/net v0.55.0, golang.org/x/text v0.37.0, golang.org/x/crypto
v0.52.0 and google.golang.org/grpc v1.83.1 are also gone, because dolt v2.4.1 no longer
reports them.

**Kept (22 rows).** The publisher has not yet shipped a fixed release for any of these
pins:

| Binary                                              | Rows | Component and finding                                                                                                     | Disposition                                                                                                                                                     |
| --------------------------------------------------- | ---: | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/usr/local/bin/dolt` (v2.4.1, latest)              |   16 | Go stdlib v1.26.2: 27145, 33811, 33814, 33818, 39820, 39821, 39822, 39836, 42499, 42504, 46600, 56853, 56858–56860, 56862 | Upstream release is built with an old Go toolchain. Used as the beads issue database server. Update when dolt ships a build with Go >= 1.26.6.                  |
| `/usr/local/bin/bd` (v1.3.1, latest)                |    3 | grpc v1.83.0 (CVE-2026-84304, CVE-2026-84445), apache/thrift v0.23.0 (CVE-2026-43871)                                     | Client of the local dolt server and the GitHub Dolt remote. Update when bd bumps grpc >= 1.83.2 and thrift >= 0.24.0.                                           |
| `docker-buildx` plugin (0.37.1, Docker trixie repo) |    3 | github.com/docker/docker v28.5.2 (CVE-2026-41567, CVE-2026-42306), github.com/moby/go-archive v0.2.0 (CVE-2026-17106)     | Vendored libraries. Buildx 0.37.2 still bundles the same versions and is not yet published in Docker's trixie apt repository (newest: 0.37.1). Bump when it is. |

## libxml2_pending

All 16 rows are kept (8 advisories × `libxml2` and `libxml2-dev`
2.12.7+dfsg+really2.9.14-2.1+deb13u3). On 5 October, the
[Debian security tracker](https://security-tracker.debian.org/tracker/source-package/libxml2)
lists every advisory as vulnerable in trixie and trixie-security. None has a fixed
trixie version. All are fixed only in forky/sid 2.15.4+dfsg-1.

| Advisory                             | Severity | Debian trixie status                    | Note                                                                      |
| ------------------------------------ | -------- | --------------------------------------- | ------------------------------------------------------------------------- |
| CVE-2026-6653                        | CRITICAL | `<no-dsa>` (minor issue)                | Use-after-free in internal subset parsing; denial of service              |
| CVE-2026-74860                       | HIGH     | vulnerable, no fix                      | Double free reachable only through the Python SAX bindings                |
| CVE-2026-86138 to 86140, 86142–86144 | HIGH     | vulnerable, no fix (Debian bug 1146744) | Overflows in dict, URI, valid, XPointer, xmlIO; XInclude flag propagation |

libxml2 is a library dependency of inherited development tooling. Nothing in the
project's workflow parses untrusted XML in the container. Remove these rows when a
trixie point or security update ships.

## openssh_pending

All 3 rows are kept (`openssh-client` 1:10.0p1-7+deb13u4). Debian marks every advisory
`<no-dsa>` (minor issue) for trixie. They are fixed only in sid (OpenSSH 10.4p1 and
later). The image contains `openssh-client` but no `openssh-server`.

| Advisory       | Severity | Applicability                                                                                                                                                 |
| -------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CVE-2026-60002 | CRITICAL | Client use-after-free, triggered only if a server changes its host key during re-key. Mitigation: connect only to trusted servers with host-key verification. |
| CVE-2026-59999 | HIGH     | sshd only (`DisableForwarding` vs `PermitTunnel`). No sshd runs in the image.                                                                                 |
| CVE-2026-60000 | HIGH     | sshd only (GSSAPI `MaxAuthTries`). No sshd runs in the image.                                                                                                 |

## pip_vendor

The group has no baseline rows since PR 499 removed the pip installations (see
[remediation](security-remediation.md)). The scan contains no setuptools or msgpack
fingerprint, so the group definition is deleted.

## Stale rows removed from the 27 October groups

The 5 October scan matched none of these 72 rows, so they are removed. The remaining
rows and the 27 October expiry are not re-reviewed here and stay as they were. Removing
a row that matches no finding cannot create an unreviewed finding.

| Group            | Rows | Package and installed version                                                                                    | Why stale                                     |
| ---------------- | ---: | ---------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `kernel_headers` |   39 | linux-libc-dev 6.12.107-1                                                                                        | Superseded by 6.12.111-1, whose 51 rows match |
| `debian_pending` |   19 | rsync 3.4.1+ds1-5+deb13u4                                                                                        | Upgraded to 3.5.0+ds1-0+deb13u1               |
| `debian_pending` |   12 | python3.13, python3.13-minimal, libpython3.13-minimal, libpython3.13-stdlib 3.13.5-2+deb13u5 (3 advisories each) | Debian Python 3.13 stack removed in PR 499    |
| `debian_pending` |    1 | python3-urllib3 2.3.0-3+deb13u2                                                                                  | Removed in PR 499                             |
| `debian_pending` |    1 | librsvg2-dev 2.60.0+dfsg-1                                                                                       | No longer installed (librsvg2-2 remains)      |
