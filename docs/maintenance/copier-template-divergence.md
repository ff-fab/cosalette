# Copier template divergence

cosalette was scaffolded from
[`ff-fab/tmpl_python_project_kickstart`](https://github.com/ff-fab/tmpl_python_project_kickstart)
and stays subscribed to it via `.copier-answers.yml` and `task update:template`.

The template assumes a **plain Python application**. cosalette is a **Rust-accelerated
Python library** with an MQTT integration suite and a hardened supply chain, so a number
of files intentionally do *not* match what the template renders. Every copier update
therefore produces conflicts in the same places.

This page is the register of those deliberate deviations. Re-apply the decisions listed
here instead of re-litigating them; if you decide something new, add a row.

| Field | Value |
| ----- | ----- |
| Template | `https://github.com/ff-fab/tmpl_python_project_kickstart` |
| Current pin | `_commit: v1.19.2` (`.copier-answers.yml`) |
| Update command | `task update:template` |
| Last reviewed | 2026-08-04 (v1.17.0 → v1.19.2) |

!!! note "Why this is not an ADR"

    ADRs in `docs/adr/` record *framework architecture* decisions and are immutable
    once accepted (amend or supersede via `task adr:create`). This page is a living
    maintenance register that gets edited on every template update, and it mostly
    *records* decisions taken elsewhere rather than making new ones. If a future
    divergence is a genuine architectural decision, raise an ADR and link it from
    the table below.

---

## Build and packaging

### Rust / maturin build backend

The template renders a pure-Python `[build-system]`. cosalette must keep:

```toml
[build-system]
requires = ["maturin>=1.12,<2"]
build-backend = "maturin"

[tool.maturin]
features = ["pyo3/extension-module", "pyo3/abi3-py314"]
python-source = "packages/src"
module-name = "cosalette._filters_rs"
manifest-path = "crates/cosalette-filters-rs/Cargo.toml"
```

Everything that hangs off this is equally out of template scope: `crates/`,
`Cargo.toml`, `Cargo.lock`, the `rust-wheels.yml` workflow, the `security:rust`
task, and `.github/requirements/maturin.txt`.

**On update:** always reject a `[build-system]` change. Verify afterwards with
`uv build` (must produce both an sdist and a `cp314-abi3` wheel) and
`uv run python -c "import cosalette._filters_rs"`.

### pydantic upper bound

`pydantic>=2.12.5,<3` — both ends are deliberate.

- **Upper bound** guards `_ConsumerAwareGenerateJsonSchema` in
  `packages/src/cosalette/_schema/_asyncapi.py`, which overrides pydantic's *private*
  `GenerateJsonSchema._sort_recursive` to preserve `consumer()` key order. A pydantic
  major could change that internal silently, so the bump must be an explicit, reviewed
  step.
- **Floor held at 2.12.5** (the template proposes `2.13.4`). cosalette ships as a
  library, so the floor is a compatibility contract for downstream users and is raised
  only when a feature actually requires it.

---

## Task runner and QA scripts

### `scripts/qa-task.sh` is a superset, not drift

The repo script implements every template subcommand plus ten more. These are
cosalette-specific gates and must survive every update:

`test:mqtt`, `test:integration:full`, `test:bench`, `security:rust`,
`security:deps:env`, `security:docker:lint`, `security:docker:scan`, `docs:build`,
`schema:check`, `template:check`

The template also renames `security:docker:lint` → `docker:lint`. **Rejected** — the
`security:` prefix groups it with the rest of the security gates and matches
`task security:audit`.

### `Taskfile.yml`

| Template proposes | Decision |
| ----------------- | -------- |
| `HAS_UNIT_TESTS` / `HAS_INTEGRATION_TESTS` guard vars | **Rejected.** Dead code here — both test trees always exist, and the guards only add a layer of string comparison that can silently skip the suite. |
| A second `# Security` section block | **Rejected.** The repo already has one (its own `# Security` block sits between `Lint & Typecheck` and `Documentation`); merging the template's would duplicate it. |
| `docker:lint` rename | **Rejected**, see above. |

---

## CI/CD workflows

### `docs.yml` deploys to surge.sh, not GitHub Pages

The template renders two jobs (`build`, `deploy`) targeting GitHub Pages. cosalette has
six — `changes`, `build`, `docs-gate`, `deploy`, `deploy-preview`, `teardown` — built
around surge.sh:

- `cosalette-main.surge.sh` for `main`
- `cosalette-pr-<N>.surge.sh` per-PR previews, torn down on close

Accepting the template version would delete four of the six jobs and rewrite the other
two.

**On update:** reject `docs.yml` wholesale.

#### `surge_token` does *not* control this

`surge_token` was corrected `false` → `true` during the v1.19.2 update, because the repo
does deploy docs to surge.sh. Be clear about what that answer actually does in the
template, so it is not mistaken for the reason `docs.yml` diverges:

- `template/.github/workflows/docs.yml.jinja` **never references `surge_token`**. It
  renders the GitHub Pages version regardless, so the conflict above happens either way.
- The answer gates exactly one thing — a *filename*:
  `template/.github/workflows/{% raw %}{% if surge_token %}docs-preview.yml{% endif %}{% endraw %}.jinja`.

**Consequence of the correction:** with `surge_token: true`, every future `copier update`
will render a **new `.github/workflows/docs-preview.yml`** that this repo does not have
and does not want — cosalette already implements PR previews and teardown as the
`deploy-preview` and `teardown` jobs *inside* `docs.yml`. Keeping both would double-deploy
to the same `cosalette-pr-<N>.surge.sh` domain.

Delete `.github/workflows/docs-preview.yml` after each update, alongside
`scripts/setup-github-remote.sh` (see below) — or add it to the same `--exclude` list.

### `ci-gate` needs

`ci-gate` in `ci.yml` keeps `integration-tests` and `codeql` in `needs:`. The template's
gate does not know about either job. Dropping them would make the required status check
pass while those gates are still red.

```yaml
needs: [lint, unit-tests, integration-tests, complexity, codeql, security]
```

### `persist-credentials: false`

Every `actions/checkout` carries `persist-credentials: false`. The single exception is
the release job in `release-please.yml` that checks out `main` with a GitHub App token
because it must push. The template omits the flag entirely.

### Container image digest pinning

GitHub Actions are SHA-pinned in both the repo and the template, so those hunks are
usually clean. **Container images are not** — the template uses bare tags:

| Image | Template | Repo |
| ----- | -------- | ---- |
| devcontainer base | `mcr.microsoft.com/devcontainers/python:3.14` | same + `@sha256:...` |
| hadolint (`qa-task.sh`) | n/a | `ghcr.io/hadolint/hadolint:v${VER}@sha256:...` |

Keep the digests. (`aquasec/trivy` is still tag-only — see the `TODO(cos-k6r)` in
`scripts/qa-task.sh`; Renovate tracks its version via `regexManagers`.)

---

## Repository configuration

### `renovate.json`

Three blocks exist only here and are dropped by a naive merge:

- **`regexManagers`** — tracks the `HADOLINT_VERSION` and `TRIVY_VERSION` shell
  variables inside `scripts/qa-task.sh`, which no built-in manager can see.
- **`pip_requirements`** with `fileMatch: ["^\\.github/requirements/[^/]+\\.txt$"]` —
  tracks the hash-pinned maturin build requirement.
- **`automerge: false` + `minimumReleaseAge: "7 days"`** on the Python and
  GitHub Actions groups, plus `automerge: false` on all majors, Dockerfile updates and
  lock-file maintenance. cosalette ships to a fleet in a wheel; nothing merges
  unreviewed.

The template also groups Dockerfile *digest* updates only; the repo requires manual
review of **all** Dockerfile updates.

### `.devcontainer/devcontainer.json`

| Setting | Template | Repo | Why |
| ------- | -------- | ---- | --- |
| `cacheFrom` | `ghcr.io/ff-fab/cosalette-devcontainer` | `...:buildcache` | BuildKit writes a `vnd.buildkit.cacheconfig.v0` manifest that a plain pull of the untagged repo cannot consume — see the `cacheTo`/`cacheFrom` comment in `devcontainer-build.yml`. |
| `workspaceFolder` | `/workspaces/cosalette` | `/workspace` | Established path; changing it invalidates every tool config, `.env`, and container volume. |

### `.pre-commit-config.yaml`

- **Local `detect-secrets` hook** — runs `uv run detect-secrets-hook --baseline
  .secrets.baseline` so the hook uses the same uv-managed version as
  `task security:secrets`, instead of a separately-versioned remote rev.
- **codespell** `--ignore-words-list hass,fpr` — `hass` (Home Assistant) and `fpr`
  (fingerprint) are domain vocabulary.
- **Excludes** for `docs/`, `docs/planning/legacy/`, and generated files.

---

## Copier itself

### `scripts/setup-github-remote.sh` is re-created on every update

`create_remote_repo: false` does **not** stop it. In the template,
`template/scripts/setup-github-remote.sh.jinja` has no `{% if create_remote_repo %}`
gate around it, and `copier.yml` has no `_exclude` entry — the answer only gates the
post-`copy` `_tasks` hook (`when: "{{ create_remote_repo and _copier_operation ==
'copy' }}"`). The file is therefore rendered unconditionally on every `copier update`.

It runs `gh repo create` and is meaningless for a repo that has existed for months, so
it is deleted from this repo and must be deleted again after each update.

**Recommended fix:** add the exclusion to `update:template` in `Taskfile.yml` —

```yaml
update:template:
  cmds:
    - uvx --python 3.14 --with jinja2-time copier update --trust
        --exclude scripts/setup-github-remote.sh
```

Tracked in `cos-lak5`. The cleaner fix is upstream: gate the file in `copier.yml`.

### `packages/src/cosalette/main.py`

A one-line `# TODO` placeholder the template re-creates. Delete it; nothing imports it.

---

## Toolchain floors

The template drives ruff and ty floors. v1.19.2 raised them to `ruff>=0.16.1` and
`ty>=0.0.65`. cosalette stays converged rather than pinning back, which means the update
carries tooling fallout:

- **ruff 0.16.1** started formatting Python code blocks inside Markdown, which reformats
  `packages/src/cosalette/assets/guidance/cosalette.instructions.md`.
- **ty 0.0.66** with the repo's `[tool.ty.rules] all = "error"` surfaced 201 errors.
  Two strict opt-in rules with an upstream default of `ignore`
  (`missing-type-argument`, `missing-override-decorator`) are scoped back to that
  default in `[tool.ty.rules]`; see the comment there. Burn-down tracked in `cos-tual`.

Note that `all = "error"` means **every ty release can add new errors**. Budget for
that on each template update, and never downgrade a core correctness rule to work
around it.

---

## Update checklist

1. `task update:template` (add `--exclude scripts/setup-github-remote.sh` until
   `cos-lak5` lands).
2. Resolve conflicts against the table above.
3. Delete these if re-created — copier renders all three unconditionally:
   `scripts/setup-github-remote.sh`, `packages/src/cosalette/main.py`, and
   `.github/workflows/docs-preview.yml` (new since `surge_token: true`).
4. `task sync && task check`.
5. `uv build` and `uv run python -c "import cosalette._filters_rs"` — the Rust
   extension is the highest-risk divergence.
6. `task pre-pr`.
7. Update the "Last reviewed" row above and add any new rows.
