# T2: SonarQube / SonarCloud Evaluation

**Status:** Open — Deliberation
**Created:** 2026-03-05
**Trigger:** Manual evaluation request

## Context

We want to evaluate whether SonarQube (or its hosted sibling, SonarCloud) would bring
meaningful value to cosalette, given our existing quality tooling stack.

### Current Quality Tooling Inventory

Before evaluating what SonarQube _adds_, here's what we already have (and it's
impressive):

| Concern                | Current Tool(s)                          | Where it runs            |
| ---------------------- | ---------------------------------------- | ------------------------ |
| **Linting**            | Ruff (E/W/F/B/I/C4/UP/SIM/ARG)          | Pre-commit, CI, IDE      |
| **Type checking**      | mypy (strict) + Pyright (IDE)            | Pre-commit, CI, IDE      |
| **Code coverage**      | pytest-cov → Codecov (80% target)        | CI, PR comments          |
| **Complexity**         | Radon + Xenon (B/A/A thresholds)         | CI                       |
| **Security (SAST)**    | GitHub CodeQL (Python)                   | CI (push/PR), weekly     |
| **Supply chain**       | SBOM (Syft/CycloneDX), Dep Submission    | CI                       |
| **Dependency updates** | Renovate                                 | Automated PRs            |
| **Formatting**         | Ruff format, Prettier (non-Python)       | Pre-commit               |
| **Spell check**        | Codespell                                | Pre-commit               |

This is a mature, well-layered quality gate. Any new tool must justify its **marginal
value** over this stack.

---

## What SonarQube/SonarCloud Offers

SonarQube is a comprehensive code quality and security analysis platform. There are
three deployment models:

| Variant                | Hosting       | Cost for open-source        | Maintenance      |
| ---------------------- | ------------- | --------------------------- | ---------------- |
| **SonarCloud**         | SaaS (hosted) | **Free** for public repos   | None (managed)   |
| **SonarQube Community**| Self-hosted   | Free (FOSS edition)         | You manage infra |
| **SonarQube (paid)**   | Self-hosted   | $$$ (Developer/Enterprise)  | You manage infra |

### SonarQube's Analysis Categories

1. **Bugs** — Likely runtime errors (null dereference, resource leaks, dead code paths)
2. **Vulnerabilities** — Security issues (injection, XSS, hardcoded secrets)
3. **Security Hotspots** — Code needing manual security review
4. **Code Smells** — Maintainability issues (complexity, duplication, naming)
5. **Coverage** — Import coverage reports and track over time
6. **Duplications** — Copy/paste detection across the codebase

---

## Option A: SonarCloud (Hosted, Free for OSS)

**What it does:** SonarCloud is the SaaS version — zero infrastructure, free for public
GitHub repos. It runs analysis on every PR and push, publishing results as PR
decorations (comments, quality gate status).

**Implementation:**

1. Sign up at sonarcloud.io with GitHub OAuth
2. Import the cosalette repository
3. Add a `sonar-project.properties` file:
   ```properties
   sonar.projectKey=ff-fab_cosalette
   sonar.organization=ff-fab
   sonar.sources=packages/src/cosalette
   sonar.tests=packages/tests
   sonar.python.coverage.reportPaths=coverage.xml
   sonar.python.version=3.14
   ```
4. Add a CI workflow step (or use the SonarCloud GitHub App for automatic analysis)
5. Configure a Quality Gate (their "Clean as You Code" default is solid)

**Advantages:**

- **Zero infrastructure** — fully managed SaaS
- **Free for public repos** — no cost for open-source projects
- **PR decorations** — inline annotations on changed lines, quality gate status check
- **Historical trends** — track quality metrics over time (debt, coverage, duplications)
- **Duplication detection** — _genuine gap_ in our current stack (no tool does this)
- **Unified dashboard** — single pane of glass for quality, coverage, security
- **Clean as You Code** — focuses analysis on new/changed code (pragmatic for
  brownfield codebases)
- **Minimal setup** — <30 minutes to full integration

**Disadvantages:**

- **Heavy overlap with existing tools:**
  - Linting: Ruff already catches most code smells SonarCloud would flag
  - Security: CodeQL is _deeper_ for security analysis than SonarCloud's Python rules
  - Coverage: Codecov already does this with better PR integration
  - Complexity: Radon/Xenon already gate this
- **Python 3.14 support uncertainty** — SonarCloud may lag behind on cutting-edge
  Python versions. As of early 2026, support for 3.14 syntax may be incomplete
- **Another PR check** — adds one more status check to PRs (CI time)
- **External dependency** — analysis results live on sonarcloud.io, not in your repo
- **Noise risk** — may flag issues Ruff/mypy already catch, creating duplicate alerts
- **Limited Python rule depth** — Sonar's Python analyzer is decent but not as deep
  as Ruff's ~800+ rules for Python-specific patterns

**Unique value (what you CAN'T get today):**

1. **Copy/paste (duplication) detection** — no current tool covers this
2. **Maintainability rating & technical debt estimation** — quantified time-to-fix
   metrics
3. **Historical trend dashboard** — visual quality trajectory over time
4. **Cognitive complexity** (Sonar's metric) — different from cyclomatic complexity
   (Radon), focuses on how hard code is to _understand_ rather than test

## Option B: SonarQube Community (Self-Hosted)

**What it does:** Run SonarQube on your own infrastructure (Docker container or VM).
Same analysis engine as SonarCloud, but you own the data and infrastructure.

**Implementation:**

1. Deploy SonarQube via Docker (or host on a VPS)
2. Configure scanner in CI (similar to Option A)
3. Manage updates, backups, uptime

**Advantages:**

- Same analysis capabilities as SonarCloud
- Data stays on your infrastructure
- No external dependency for results

**Disadvantages:**

- **Infrastructure burden** — must host, maintain, patch, and back up a JVM-based
  server with a database (PostgreSQL). Completely disproportionate for a single
  open-source Python project
- **Cost** — even "free" means compute resources ($5-20/month for a VPS)
- **No meaningful advantage over SonarCloud** for a public repo — SonarCloud is free
  and requires zero maintenance
- All the same overlap/noise concerns as Option A

**Verdict:** _Not recommended_ — SonarCloud is strictly superior for a public
open-source project. Self-hosting adds cost and toil with no benefit.

## Option C: Don't Add SonarQube — Address Gaps Individually

**What it does:** Instead of adding a broad platform, surgically fill the specific gaps
in the current stack using lightweight, focused tools that fit the existing
Ruff/pytest/pre-commit ecosystem.

**Addressing the gaps SonarQube would fill:**

| Gap                 | Lightweight solution                                        |
| ------------------- | ----------------------------------------------------------- |
| **Duplication**     | Add `jscpd` (copy/paste detector) as a CI step or task     |
| **Cognitive complexity** | Already partially covered by `flake8-cognitive-complexity` (in dev deps!) |
| **Trend dashboard** | Codecov already tracks coverage trends; GitHub Insights for general metrics |
| **Debt estimation** | Accept this as a gap — or use Radon's maintainability index |

**Implementation (duplication detection with jscpd):**

```bash
# Install
npm install -g jscpd

# Run
jscpd packages/src/cosalette --min-lines 5 --min-tokens 50 --reporters console
```

Or add a `flake8-cognitive-complexity` max threshold to enforce cognitive complexity
alongside Radon's cyclomatic complexity.

**Advantages:**

- **No new platform** — stays within the existing tool ecosystem
- **Faster CI** — lightweight tools vs. a full Sonar analysis pass
- **No external dependencies** — all results stay local/in-CI
- **Targeted** — only adds what's actually missing
- **Simpler stack** — easier for contributors to understand
- **Already have flake8-cognitive-complexity** — it's in `dev` deps but hasn't been
  wired into the quality gate yet

**Disadvantages:**

- **No unified dashboard** — metrics remain spread across Codecov, GitHub, CI logs
- **No time-based debt estimation** — "this smell would take ~2h to fix" is a
  SonarQube-specific feature
- **Manual curation** — you decide thresholds rather than getting Sonar's opinionated
  defaults
- **Missing trend visualization** — no historical quality trajectory chart

---

## Analysis and Recommendation

### Overlap Assessment

Here's a honest overlap matrix — what percentage of SonarQube's value is already covered:

| SonarQube category  | Current coverage | Tools covering it              |
| ------------------- | ---------------- | ------------------------------ |
| Bugs                | ~85%             | Ruff (F/B), mypy, Pyright     |
| Vulnerabilities     | ~90%             | CodeQL (deeper than Sonar)     |
| Security Hotspots   | ~70%             | CodeQL (different methodology) |
| Code Smells         | ~80%             | Ruff (SIM/C4/UP/ARG), Radon   |
| Coverage tracking   | ~95%             | Codecov                        |
| Duplications        | **0%**           | _Nothing_                      |
| Trend dashboard     | ~40%             | Codecov (coverage only)        |

### The Real Question

SonarQube/SonarCloud's **unique, non-overlapping value** for this project boils down to:

1. Duplication detection
2. Cognitive complexity (partially covered by existing dep)
3. Historical trend dashboard
4. Technical debt time estimation

Against these costs:

- Another external platform to manage/monitor
- Potential duplicate noise from overlapping analysis
- Python 3.14 support risk
- One more CI check adding to PR feedback time

### Recommendation: **Option C** (address gaps individually)

**Why:** The overlap is too high (~80%+) to justify adding an entire platform. The
genuine gaps (duplication, cognitive complexity) can be filled with lightweight tools
that fit your existing ecosystem philosophy — focused tools composed via Taskfile,
pre-commit, and CI.

**However**, if you value the _unified dashboard_ and trend visualization highly — or
plan to scale to multiple repos — **Option A (SonarCloud)** becomes a reasonable
choice. It's free, zero-maintenance, and the setup cost is genuinely small (~30 min).
The main risk is alert fatigue from overlapping detections.

### If You Choose SonarCloud (Option A)

Key decisions:
- Run Sonar analysis in CI _in addition to_ existing checks (don't replace anything)
- Configure Sonar to import your coverage.xml rather than re-analyzing coverage
- Set Sonar's quality gate as _informational_ (non-blocking) initially, to assess noise
- Tune rule profiles to disable rules already enforced by Ruff/mypy to reduce duplicates

### If You Choose Option C

Immediate actions:
1. Add `jscpd` for duplication detection (Taskfile task + CI step)
2. Wire `flake8-cognitive-complexity` into the quality gate (it's already installed!)
3. Consider Radon's maintainability index as a complement to complexity

---

## Decision

**Option C selected** — 2026-03-05

We chose to address gaps individually rather than introducing SonarQube/SonarCloud:

1. **Cognitive complexity**: Wired `flake8-cognitive-complexity` into the quality gate
   (threshold 15, matching Sonar's default). Four pre-existing violations suppressed
   with `noqa` and tracked for future refactoring.
2. **Duplication detection**: Added Pylint's `symilar` (pure Python, no Node.js
   dependency). Threshold: 6 lines minimum, ignoring imports and signatures.
3. Both checks integrated into `task complexity` (and thus CI) as subtasks.

## Next Steps

- [x] Wire cognitive complexity into quality gate
- [x] Add duplication detection (symilar)
- [ ] Refactor the 4 suppressed high-complexity functions (future work)
