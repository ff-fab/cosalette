# Security Policy

Security audit documentation (threat model, charter, findings register) is maintained
privately. Contact maintainers through the vulnerability reporting process below.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.8.x | :white_check_mark: |
| 0.7.x | :x:                |
| 0.6.x   | :x:                |
| 0.5.x   | :x:                |
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

## Developer-tool trust boundary (`module:app` specs)

Several developer commands import an application or settings object that you name as a
`module.path:attribute` spec:

- `cosalette manifest <module:app>` and the `cosalette schema` commands
- the MCP introspection / configuration / scaffolding tools

**Importing a Python module executes its top-level code.** These specs are a _trust
boundary_, exactly like `uvicorn module:app` or `gunicorn module:app`: the named module
runs with your privileges _before_ any "is this really an App?" check can reject it.

- **Do not run `cosalette manifest` / `cosalette schema` against a repository or spec
  you do not trust** — for example while reviewing an untrusted pull request or a
  third-party repo. Read the code first, or run it in a sandbox/container.
- The **MCP server** additionally gates these imports behind an allowlist: set
  `COSALETTE_MCP_IMPORT_ALLOW` to your app's module prefix(es); with it unset, every MCP
  import is refused. See the
  [MCP Server guide](docs/guides/mcp-server.md#security-introspection-imports-your-app).
- cosalette's own MQTT data plane never imports or evaluates broker payloads; this
  boundary is specific to the developer tooling listed above.

## Dependencies

We monitor dependencies for known vulnerabilities via:

- **Renovate** — scheduled dependency freshness updates (weekly), with vulnerability
  alerts enabled so vulnerable dependencies get update PRs as advisories are published
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
