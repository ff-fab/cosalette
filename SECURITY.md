# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

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

- **MQTT broker authentication** — always require TLS and credentials for your broker.
- **Network segmentation** — isolate device networks from public-facing services.
- **Input validation** — cosalette validates handler parameters, but adapter
  implementations should sanitise device-level data before publishing.

## Dependencies

We monitor dependencies for known vulnerabilities via:

- **Dependabot alerts** — automated CVE scanning of the dependency graph
- **Dependabot security updates** — automatic PRs for vulnerable dependencies
- **Renovate** — scheduled dependency freshness updates (weekly)
