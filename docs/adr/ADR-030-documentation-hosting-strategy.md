# ADR-030: Documentation Hosting Strategy

## Status

Accepted **Date:** 2026-04-05

## Context

cosalette's documentation is built with MkDocs and was previously deployed to GitHub
Pages on every push to `main`. The published documentation therefore always reflects
the development branch, not the latest release. Users following the official docs may
encounter features, APIs, or configuration options that have not yet been included in
any published package version.

A secondary problem: PR authors have no way to preview documentation changes before
merging. Reviewing rendered MkDocs output from raw Markdown is error-prone, especially
for navigation changes and content involving custom macros or diagrams.

### Constraints

- cosalette releases are infrequent relative to `main` commits — the gap between
  what users run and what docs describe can be significant.
- GitHub Pages supports only a single deployment per repository on free plans,
  ruling out branch-based multi-deployment strategies.
- The project already uses surge.sh for preview deployments in cosalette-apps —
  a proven pattern for ephemeral hosting.

## Decision

Adopt a **three-tier documentation hosting strategy**:

| Tier | Host | URL | Updated When |
| ---- | ---- | --- | ------------ |
| PR Preview | surge.sh | `cosalette-pr-{N}.surge.sh` | Every PR push; torn down on PR close |
| Main (dev) | surge.sh | `cosalette-main.surge.sh` | Every push to `main` |
| Official (release) | GitHub Pages | `ff-fab.github.io/cosalette/` | Only on release-please releases |

### 1. PR Preview — surge.sh

A CI job deploys the built MkDocs site to `cosalette-pr-{N}.surge.sh` (where `N` is
the PR number) on every push to a pull request. A bot comment is created or updated on
the PR with the preview URL using `peter-evans/find-comment@v3` and
`peter-evans/create-or-update-comment@v4`, maintaining a single comment per PR rather
than appending a new one on every push.

When the PR is closed or merged, a `pull_request_target` job tears down the deployment
by publishing a blank page to the same subdomain. `pull_request_target` is used for
teardown because it runs in the base-branch context where `SURGE_TOKEN` is available.
The teardown job never executes PR code — it only deploys a static empty directory
using the PR number extracted from the event context, eliminating the trusted-context
risk associated with `pull_request` workflows.

Fork PRs gracefully skip the preview deploy: `SURGE_TOKEN` is not exposed to fork
workflows, so the deploy step is skipped without failing the run.

### 2. Main (dev) — surge.sh

A CI job deploys to `cosalette-main.surge.sh` on every push to `main`. This provides
contributors and maintainers a stable link to current development documentation without
polluting the official URL.

### 3. Official (release) — GitHub Pages

The GitHub Pages deployment is decoupled from `main` pushes and triggered exclusively
by release-please release events. The official URL (`ff-fab.github.io/cosalette/`)
always reflects the latest published release, not the development branch.

## Decision Drivers

- Users reading official docs must not encounter unreleased features
- PR authors need rendered doc previews before merge
- surge.sh is already used by cosalette-apps — reuse a proven pattern over
  introducing a new service
- GitHub Pages free tier permits only one deployment per repository
- `pull_request_target` teardown safely handles credential access without
  executing untrusted PR code

## Considered Options

| Option | Description | Pros | Cons | Chosen |
| ------ | ----------- | ---- | ---- | ------ |
| **A: Three-tier (PR preview + dev + release gate)** | PR preview on surge.sh, dev on surge.sh, release on Pages | Solves all three problems; proven pattern | Requires `SURGE_TOKEN` secret; three CI jobs | **Yes** |
| B: Status quo (single Pages deployment on `main` push) | Single deployment updated on every `main` push | Zero additional setup | Docs always ahead of releases; no PR preview | No |
| C: Versioned docs (mike / MkDocs-versioning) | Version switcher with full history | Proper version selector UI | Persistent hosted state required; significant tooling overhead | No |
| D: Two-tier only (no PR previews) | Release gate only, no per-PR preview | Simpler CI than Option A | PR authors cannot preview doc changes | No |
| E: Branch-based GitHub Pages | Multiple branches mapped to Pages paths | Pure GitHub, no third-party | Requires Pages Pro/Teams; not available on free plan | No |

## Consequences

### Positive

- The official URL always matches the latest published release — unreleased
  features are not surfaced to users following official docs
- PR authors receive a rendered preview URL as a bot comment on every push,
  eliminating the need to build docs locally before review
- `cosalette-main.surge.sh` provides a stable dev-docs link for contributors
  without interfering with the official URL
- Teardown on PR close avoids accumulating stale surge deployments over time
- Fork PRs are handled gracefully — no CI failure when `SURGE_TOKEN` is absent

### Negative

- `SURGE_TOKEN` must be configured as a repository secret; deployments on fork
  PRs are silently skipped
- Three separate CI jobs (pr-preview, main-deploy, release-deploy) add marginal
  pipeline complexity compared to a single deploy job
- surge.sh is a third-party service dependency for two of the three tiers — an
  outage or pricing change would affect PR previews and dev docs (the official
  GitHub Pages tier is unaffected)

_2026-04-05_
