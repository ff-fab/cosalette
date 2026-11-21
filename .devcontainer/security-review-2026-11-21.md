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

The three CRITICAL package fingerprints are `CVE-2026-60002` in `openssh-client` and
`CVE-2026-6653` in both `libxml2` and `libxml2-dev`. Debian lists the installed Trixie
versions as vulnerable and records no Trixie fix for either advisory at review time. The
OpenSSH issue affects the client when a server changes its host key during rekeying; the
libxml2 issue involves processing malicious XML. Avoid untrusted SSH servers and
untrusted XML while these components remain vulnerable. See the Debian trackers for
[CVE-2026-60002](https://security-tracker.debian.org/tracker/CVE-2026-60002) and
[CVE-2026-6653](https://security-tracker.debian.org/tracker/CVE-2026-6653).

## Remaining work

The 222 changed or new fingerprints include 40 Linux header attributions and 140
Kerberos package attributions across 14 advisory IDs. Header attribution does not
evaluate the host kernel; `cos-dam4` tracks that separate question. Debian's
[Kerberos tracker](https://security-tracker.debian.org/tracker/source-package/krb5)
lists the 14 advisories as open for Trixie. The image inventory shows curl and OpenSSH
require the Kerberos runtime. PostgreSQL and curl development packages pull in
additional Kerberos administration libraries; PR #571 removes those unused development
dependencies and must be rebuilt and rescanned to verify the effect and the native
toolchain.

Reassess every remaining fingerprint and existing acceptance using the final scan,
vendor status, package contents, and reachable use before updating the baseline. Remove
stale rows and use narrow, owned, expiring entries only when their disposition is
supported. Verify the resulting scan has no unreviewed HIGH/CRITICAL findings and no
secrets. Keep `cos-nxu6` open for continuing remediation and `cos-dam4` open for
host-kernel applicability.
