# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.5.x   | :white_check_mark: |
| 0.4.x   | :x:                |
| 0.3.x   | :x:                |
| 0.2.x   | :x:                |
| 0.1.x   | :x:                |

Only the latest release receives security updates. Older minor versions are not
backported.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, use **GitHub Private Vulnerability Reporting** — click
[Report a vulnerability](https://github.com/ff-fab/cosalette/security/advisories/new) on
the Security tab. This creates a private advisory visible only to maintainers.

### What to Include

- Description of the vulnerability
- Steps to reproduce or a proof of concept
- Affected versions (if known)
- Potential impact assessment

### What to Expect

- **Acknowledgement** within 48 hours
- **Triage and initial assessment** within 7 days
- **Fix or mitigation** targeting the next patch release
- **Public disclosure** coordinated with the reporter after the fix is released

We follow [responsible disclosure](https://en.wikipedia.org/wiki/Responsible_disclosure)
practices. If you report a vulnerability, we will credit you in the release notes
(unless you prefer to remain anonymous).

## Security Considerations for IoT Deployments

cosalette bridges IoT devices to MQTT. When deploying, consider:

- **MQTT broker authentication** — require named users and broker-side ACLs scoped to
  each app's topic prefix.
- **MQTT transport security** — expose plaintext MQTT only on localhost or private
  container networks. Use broker TLS on `8883` for cross-host traffic and set
  `MQTT__TLS=true` plus `MQTT__TLS_CA_FILE` in the app configuration.
- **Network segmentation** — isolate device networks from public-facing services.
- **Retained topics** — treat retained MQTT messages as persisted data visible to any
  principal with matching subscribe ACLs.
- **Input validation** — cosalette validates handler parameters, but adapter
  implementations should sanitise device-level data before publishing.

## Dependencies

We monitor dependencies for known vulnerabilities via:

- **Dependabot alerts** — automated CVE scanning of the dependency graph
- **Dependabot security updates** — automatic PRs for vulnerable dependencies
- **Renovate** — scheduled dependency freshness updates (weekly)
- **`task security:audit`** — local and CI gate covering dependency audit, secret
  scanning, Python security linting, and GitHub Actions hardening checks

Third-party GitHub Actions are pinned to full commit SHAs. Scheduled security CI also
runs weekly so new advisory data is checked even when application code has not changed.

## Container Security

The project's devcontainer image (`ghcr.io/ff-fab/cosalette-devcontainer`) is built
weekly and scanned for vulnerabilities. The base image is **pinned by digest** to
prevent supply-chain attacks on mutable tags; Renovate automatically opens PRs to update
the digest when Microsoft publishes new base image versions.

The build workflow enforces:

- **Dockerfile linting** — hadolint runs before the image is built, failing the workflow
  on any warning-level violations (DL* Dockerfile rules and SC* ShellCheck rules).
- **Image vulnerability scanning** — Trivy scans the published image after build with a
  `HIGH,CRITICAL` severity threshold. The workflow fails if vulnerabilities are found.
- **Scheduled rescans** — the weekly devcontainer build (Monday 06:00 UTC) picks up new
  CVEs disclosed since the last build, failing the workflow if the base image or
  installed packages have new high-severity vulnerabilities.

**Local validation:**

- `task security:docker:lint` — lint `.devcontainer/Dockerfile` with hadolint
- `task security:docker:scan` — scan the devcontainer image with Trivy (requires Docker)

For production deployments, see the
[Docker hardening guidance](docs/guides/deployment.md#docker-hardening) in the
deployment guide.
