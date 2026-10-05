# Development image security remediation

The [5 October review](security-review-2026-10-05.md) records all 64 previously
unreviewed fingerprints from PR 499, with individual recommendations and sources. It is
a historical scan snapshot; use the final image scan for current inventory.

## Python installation ownership

The pinned base image carries virtualenv 21.7.9 in both
`/usr/local/py-utils/venvs/virtualenv` and `/usr/local/py-utils/venvs/pipenv`. The
latter also carries pipenv's patched pip vendor tree. Global pip and pipx's shared pip
installation carry msgpack 1.1.2, setuptools 70.3.0 and urllib3 2.7.0. These copies are
independent of the globally upgraded packages. Their vendor manifests and environment
directories were inspected in a disposable container.

The project uses uv. Remove unused pipenv, virtualenv, pipx and shared/global pip
installations rather than modifying vendor namespaces or hiding metadata. Retained
base-image linters have separate environments; verify their executables still run.
Project environments, dependencies and package installs use uv.

HTTPie 3.2.4 declares pip as a dependency, so even an isolated uv tool installation
reintroduces its vulnerable vendor tree. The optional HTTPie utility is removed; curl
remains available for HTTP requests. The separate Debian Python 3.13 stack and its
unused consumers are removed explicitly. Do not use broad apt autoremove: ICU is
required by bd and development headers are required for native builds. Removal changes
the available base-image utilities: HTTPie, Mercurial and pip-based package managers are
intentionally absent.

## Header attribution and retained host risk

All 51 kernel advisory/package pairs already existed in the acceptance baseline for
linux-libc-dev 6.12.107-1. The reviewed update changes only those pairs to 6.12.111-1.
Their scanner severity and no-fixed-version fields are preserved; the 27 October 2026
expiry is unchanged. No executable Python findings are added to the baseline.

The package file inventory includes userspace headers under `/usr/include` and
`/usr/lib/linux/uapi`, documentation and package metadata. It supplies no running kernel
or loadable implementation of the affected subsystems. The pinned base has no installed
linux-image or linux-modules package. Final image verification must repeat this
inventory and native C/Rust build smoke checks.

This image attribution does not establish host non-applicability. The inspection host
reports `6.18.33.2-microsoft-standard-WSL2`; vendor backport status and other
developers' and CI runners' kernels have not been verified. The individual review lists
the GPU, filesystem, network, architecture and other subsystem checks required for each
advisory. Issue cos-dam4 tracks that host assessment.

Docker-in-Docker currently needs privileged mode for its mount/cgroup startup. Disabling
the flag without a working replacement breaks the documented workflow. Issue cos-2jj7
tracks a validated restricted/rootless or reviewed remote builder. Mounting a Docker
socket is not a substitute for a privilege assessment because it grants control of that
daemon. The security owner issue cos-nxu6 is reopened and depends on both follow-ups; it
must remain open after this PR merges.

## Evidence and validation

Run `task build:devcontainer` and scan the resulting image with
`DOCKER_SCAN_IMAGE=cosalette-devcontainer-pr-validation:latest task security:docker:scan`.
Set `DOCKER_SCAN_REPORT` to retain sanitized package-location evidence. CI uploads that
vulnerability-only report for seven days, including failed scans. Secret matches and
image environment values are excluded; the original secret scan still runs and fails the
gate for any finding.

Require zero unreviewed HIGH/CRITICAL fingerprints and zero secrets. Reviewed findings
remain visible; changed versions, available fixes and expired entries still fail the
existing policy. Run `task test:security:docker:policy`, `task security:docker:lint`,
native build smoke checks and `task pre-pr` before merging. Complete the
memory-footprint issues only after merge, and retain open security follow-ups until
their own acceptance criteria are satisfied.
