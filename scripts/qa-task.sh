#!/usr/bin/env bash
# qa-task.sh — Durable log/status wrapper for cosalette QA tasks.
#
# Usage:
#   bash scripts/qa-task.sh <task-name> [extra-args...]
#
# Built-in implementations exist for every named QA task.  Extra args are
# forwarded to tasks that accept them (test:file, schema:check).  For unknown
# task names, extra args are treated as the command to execute directly.
#
# Environment overrides:
#   QA_LOG_DIR      directory for log/status files  (default: /tmp)
#   QA_TAIL_LINES   lines to tail on completion      (default: 160)
#   QA_TIMEOUT      overall deadline, e.g. "10m"     (default: per-task)
#   QA_NO_WRAP      if non-empty, run impl directly without log/status/timeout
#   PKG             package root relative to repo    (default: packages)
#   MODULE_NAME     Python module name               (default: cosalette)
#
# Backward-compat env aliases honoured for pre-pr:
#   PRE_PR_LOG / PRE_PR_STATUS / PRE_PR_TAIL_LINES
#
# Top-level invocations get a durable log, a .status file, and an overall
# timeout.  Composite tasks (security:audit, complexity, check, etc.) call
# run_raw_task() which sets QA_NO_WRAP=1 — child processes skip the wrapper
# entirely, streaming directly into the parent log.  This prevents nested
# durable wrappers and the "stuck inside child wrapper" failure mode.

# Guard: if sourced instead of executed, re-run as a child process so that
# set -euo pipefail cannot pollute the caller's shell and a failing task
# cannot terminate it.  The exit status is still captured by the wrapper.
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    bash "${BASH_SOURCE[0]}" "$@"
    return $?
fi

set -euo pipefail

TASK_NAME="${1:?Usage: qa-task.sh <task-name> [extra-args...]}"
shift || true

_PKG="${PKG:-packages}"
_MOD="${MODULE_NAME:-cosalette}"

# ---------------------------------------------------------------------------
# run_raw_task: invoke a sub-task's built-in implementation with QA_NO_WRAP=1
# so no nested log/status/timeout wrapper is created.  Used by composite arms.
# ---------------------------------------------------------------------------
run_raw_task() {
    QA_NO_WRAP=1 bash "${BASH_SOURCE[0]}" "$@"
}

# ---------------------------------------------------------------------------
# Per-task default timeouts — KEEP IN SYNC with the _run_impl arms below.
# When adding a new task, add both a case arm and a timeout entry here.
# ---------------------------------------------------------------------------
_task_timeout() {
    case "${TASK_NAME}" in
        pre-pr|test:integration:full)                        echo "60m" ;;
        test|test:unit|test:integration|test:mqtt|test:cov)  echo "20m" ;;
        *)                                                   echo "10m" ;;
    esac
}

# ---------------------------------------------------------------------------
# Built-in implementations — one case arm per supported QA task.
# When adding a new task, also add a timeout entry in _task_timeout() above.
# ---------------------------------------------------------------------------
_run_impl() {
    case "${TASK_NAME}" in

        test)
            uv run pytest \
                "${_PKG}/tests/unit/" "${_PKG}/tests/integration/" \
                -v --tb=short -m "not mqtt" \
                --junitxml=results-unit.xml \
                --cov="${_MOD}" --cov-branch --cov-report=json \
                || true
            uv run python "${_PKG}/tests/scripts/summarize_tests.py" \
                --coverage-file=coverage.json
            ;;

        test:unit)
            uv run pytest "${_PKG}/tests/unit/" \
                -v --tb=short --junitxml=results-unit.xml
            ;;

        test:file)
            if [ "$#" -eq 0 ]; then
                printf 'usage: qa-task.sh test:file <file_or_pattern...>\n' >&2
                return 1
            fi
            uv run pytest "$@" -v --tb=short
            ;;

        test:integration)
            uv run pytest "${_PKG}/tests/integration/" \
                -v --tb=short -m "integration and not mqtt" \
                --junitxml=results-integration.xml
            ;;

        test:integration:full)
            run_raw_task test:integration || true
            run_raw_task test:mqtt || true
            if [ ! -f results-integration.xml ] || [ ! -f results-mqtt.xml ]; then
                echo "Integration test suite incomplete - missing result files" >&2
                return 1
            fi
            if grep -q '<failure\|<error' results-integration.xml results-mqtt.xml 2>/dev/null; then
                echo "Integration test suite failed - check results-integration.xml and results-mqtt.xml" >&2
                return 1
            fi
            ;;

        test:mqtt)
            uv run pytest \
                "${_PKG}/tests/integration/test_mqtt_integration.py" \
                -v --tb=short -m mqtt --junitxml=results-mqtt.xml
            ;;

        test:bench)
            uv run pytest "${_PKG}/tests/benchmarks/" \
                -v --benchmark-enable -m benchmark
            ;;

        test:cov)
            uv run pytest "${_PKG}/tests/" \
                --cov="${_MOD}" --cov-branch \
                --cov-report=term-missing --cov-report=xml
            ;;

        lint)
            uv run ruff check "${_PKG}/src/" "${_PKG}/tests/" || return
            uv run ruff format --check "${_PKG}/src/" "${_PKG}/tests/"
            ;;

        typecheck)
            uv run ty check
            ;;

        check)
            run_raw_task lint || return
            run_raw_task typecheck || return
            run_raw_task test
            ;;

        pre-pr)
            bash scripts/pre-pr.sh
            ;;

        complexity)
            run_raw_task complexity:cyclomatic || return
            run_raw_task complexity:cognitive || return
            run_raw_task similarity
            ;;

        complexity:cyclomatic)
            uv run radon cc "${_PKG}/src/${_MOD}" \
                --average --show-complexity || return
            uv run xenon "${_PKG}/src/${_MOD}" \
                --max-absolute B --max-modules A --max-average A
            ;;

        complexity:cognitive)
            uv run flake8 --select=CCR001 "${_PKG}/src/${_MOD}/"
            ;;

        similarity)
            FILE_COUNT=$(find "${_PKG}/src/${_MOD}" -name '*.py' | wc -l)
            if [ "${FILE_COUNT}" -gt 150 ]; then
                printf 'similarity: %d files — scan time grows O(n²) above 150; consider scoping\n' \
                    "${FILE_COUNT}" >&2
            fi
            OUTPUT=$(find "${_PKG}/src/${_MOD}" -name '*.py' -print0 \
                | xargs -0 uv run symilar -d 8 \
                    --ignore-imports --ignore-signatures) || return
            echo "$OUTPUT"
            DUPES=$(printf '%s\n' "$OUTPUT" \
                | sed -n 's/.*duplicates=\([0-9][0-9]*\).*/\1/p' \
                | head -n1)
            if [ "${DUPES:-0}" -gt 0 ]; then
                echo "❌ Found $DUPES duplicate lines" >&2
                return 1
            fi
            ;;

        security:audit)
            run_raw_task security:deps || return
            run_raw_task security:rust || return
            run_raw_task security:secrets || return
            run_raw_task security:python || return
            run_raw_task security:actions || return
            run_raw_task security:docker:lint || return
            run_raw_task security:deps:env
            ;;

        security:deps)
            uv export --format requirements-txt \
                --all-extras --all-groups --no-emit-project \
                | uv run pip-audit -r /dev/stdin \
                    --strict --progress-spinner off
            ;;

        security:deps:env)
            # DEP-01: install exactly from the lock (fails if the lock is stale
            # relative to pyproject), then audit the INSTALLED environment — not
            # just the exported lock. Catches env-vs-lock drift where a stale or
            # extra installed package carries a CVE the lock-only audit misses.
            uv sync --frozen --all-extras --all-groups
            uv run --no-sync pip-audit --strict --progress-spinner off
            ;;

        security:rust)
            cargo audit --file Cargo.lock
            ;;

        security:secrets)
            if [ "${CI:-}" = "true" ]; then
                # CI fast path: scan only files changed relative to the merge base.
                # Fetch main into FETCH_HEAD (no local branch or tracking-ref
                # assumptions) so merge-base works on a shallow single-branch checkout.
                git fetch --depth=1 origin main 2>/dev/null || true
                _base=$(git merge-base HEAD FETCH_HEAD 2>/dev/null || echo HEAD~1)
                _changed_nul=$(git diff --name-only -z "${_base}" HEAD 2>/dev/null || true)
                if [ -n "${_changed_nul}" ]; then
                    printf '%s' "${_changed_nul}" \
                        | xargs -0 uv run detect-secrets-hook \
                            --baseline .secrets.baseline --
                else
                    echo "security:secrets: no changed files — skipping hook"
                fi
            else
                # Local full scan: check all tracked and untracked (non-ignored) files.
                # git ls-files -z gives portable null-delimited output and honours
                # .gitignore, so build artefacts and vendored dirs are never scanned.
                { git ls-files -z; git ls-files --others --exclude-standard -z; } \
                    | sort -zu \
                    | xargs -0 uv run detect-secrets-hook \
                        --baseline .secrets.baseline --
            fi
            ;;

        security:python)
            uv run ruff check --select S "${_PKG}/src/${_MOD}"
            ;;

        security:actions)
            uv run actionlint || return
            uv run zizmor --min-severity high --min-confidence high \
                --no-progress .github/workflows .github/actions
            ;;

        security:docker:lint)
            # Lint Dockerfiles with hadolint via Docker (no install required).
            # Pinned to specific version for reproducibility; update via Renovate.
            # Exit on warning-level violations (DL* Dockerfile rules and SC* ShellCheck rules).
            # failure-threshold=warning: exit on warning-level and above (error, warning)
            # but not info-level messages.
            HADOLINT_VERSION="${HADOLINT_VERSION:-2.12.0}"
            if ! command -v docker >/dev/null 2>&1; then
                if [ "${CI:-}" = "true" ]; then
                    echo "security:docker:lint: Docker required in CI but not found" >&2
                    return 1
                fi
                echo "security:docker:lint: Docker not available — skipping (set CI=true to fail)" >&2
                return 0
            fi
            docker run --rm -i \
                "ghcr.io/hadolint/hadolint:v${HADOLINT_VERSION}@sha256:9259e253a4e299b50c92006149dd3a171c7ea3c5bd36f060022b5d2c1ff0fbbe" \
                hadolint --no-color --failure-threshold warning - < .devcontainer/Dockerfile
            ;;

        security:docker:scan)
            # Scan the devcontainer image with Trivy for vulnerabilities.
            # Scan the local Docker daemon image by default (works after devcontainers/ci --load).
            # Override with DOCKER_SCAN_IMAGE to scan a remote registry image.
            # Exit on HIGH,CRITICAL findings.
            # TODO(cos-k6r): pin aquasec/trivy to digest once 0.59.2 manifest is available; Renovate will track version bumps via regexManagers
            TRIVY_VERSION="${TRIVY_VERSION:-0.59.2}"
            SCAN_IMAGE="${DOCKER_SCAN_IMAGE:-ghcr.io/ff-fab/cosalette-devcontainer:latest}"
            if ! command -v docker >/dev/null 2>&1; then
                if [ "${CI:-}" = "true" ]; then
                    echo "security:docker:scan: Docker required in CI but not found" >&2
                    return 1
                fi
                echo "security:docker:scan: Docker not available — skipping (set CI=true to fail)" >&2
                return 0
            fi
            echo "security:docker:scan: Scanning ${SCAN_IMAGE} with Trivy ${TRIVY_VERSION}"
            docker run --rm \
                -v /var/run/docker.sock:/var/run/docker.sock \
                "aquasec/trivy:${TRIVY_VERSION}" \
                image --severity HIGH,CRITICAL --exit-code 1 \
                --no-progress "${SCAN_IMAGE}"
            ;;

        docs:build)
            uv run --group docs zensical build --clean || return
            if [ ! -f site/index.html ]; then
                echo "ERROR: Documentation build produced no pages (site/index.html missing)" >&2
                return 1
            fi
            uv run docs/postprocess.py
            ;;

        template:check)
            uv run pytest \
                "${_PKG}/tests/unit/mcp/test_mcp_scaffolding.py" \
                -k "TestTemplateSmokeTests" -v
            ;;

        schema:check)
            uv run cosalette schema check "$@"
            ;;

        *)
            if [ "$#" -eq 0 ]; then
                printf 'qa-task.sh: no built-in for %s and no command given\n' \
                    "${TASK_NAME}" >&2
                return 1
            fi
            "$@"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# QA_NO_WRAP mode: run impl directly, streaming to the caller's stdout/stderr.
# Used internally by run_raw_task() and composite task arms to prevent nesting
# durable wrappers inside other durable wrappers.
# ---------------------------------------------------------------------------
if [[ -n "${QA_NO_WRAP:-}" ]]; then
    rc=0
    _run_impl "$@" || rc=$?
    exit "${rc}"
fi

_qa_timeout="${QA_TIMEOUT:-$(_task_timeout)}"

# Sanitize task name for filenames: replace :, /, and space with -
SAFE_NAME="${TASK_NAME//[:\/ ]/-}"

_qa_log_dir="${QA_LOG_DIR:-/tmp}"
_qa_tail="${QA_TAIL_LINES:-160}"

if [ "${TASK_NAME}" = "pre-pr" ]; then
    _log="${PRE_PR_LOG:-${_qa_log_dir}/cosalette-pre-pr.log}"
    _status="${PRE_PR_STATUS:-${_qa_log_dir}/cosalette-pre-pr.status}"
    _qa_tail="${PRE_PR_TAIL_LINES:-${_qa_tail}}"
else
    _log="${_qa_log_dir}/cosalette-${SAFE_NAME}.log"
    _status="${_qa_log_dir}/cosalette-${SAFE_NAME}.status"
fi

# Prefer gtimeout (GNU coreutils on macOS via Homebrew) over BSD timeout.
# If neither is available, run the task without a deadline and warn.
if command -v gtimeout >/dev/null 2>&1; then
    _tc="gtimeout"
elif command -v timeout >/dev/null 2>&1; then
    _tc="timeout"
else
    _tc=""
fi

# ---------------------------------------------------------------------------
# Wrapper: run impl under timeout with QA_NO_WRAP=1 (prevents re-entry),
# capture output, write status file, tail log.
# ---------------------------------------------------------------------------
# Private per-run log directory: umask 077 ensures other local users cannot
# read or write the log/status files, mitigating predictable-/tmp-path risks.
(umask 077; mkdir -p "$(dirname "${_log}")" "$(dirname "${_status}")")
printf '%s: log -> %s | status -> %s | timeout -> %s\n' \
    "${TASK_NAME}" "${_log}" "${_status}" "${_qa_timeout}"

rc=0
if [ -n "${_tc}" ]; then
    "${_tc}" --foreground --kill-after=30s "${_qa_timeout}" \
        env QA_NO_WRAP=1 PYTHONUNBUFFERED=1 bash "${BASH_SOURCE[0]}" "${TASK_NAME}" "$@" \
        >"${_log}" 2>&1 || rc=$?
else
    printf 'WARNING: gtimeout/timeout not found — running %s without a deadline\n' \
        "${TASK_NAME}" >&2
    env QA_NO_WRAP=1 PYTHONUNBUFFERED=1 bash "${BASH_SOURCE[0]}" "${TASK_NAME}" "$@" \
        >"${_log}" 2>&1 || rc=$?
fi

printf '%s\n' "${rc}" >"${_status}"

if [ "${rc}" -eq 124 ] || [ "${rc}" -eq 137 ]; then
    printf '%s: TIMEOUT after %s\n' "${TASK_NAME}" "${_qa_timeout}" >&2
fi
printf '%s: exit %s -- last %s lines of %s:\n' \
    "${TASK_NAME}" "${rc}" "${_qa_tail}" "${_log}"
tail -n "${_qa_tail}" "${_log}"

# Propagate the real exit code — timeout (124/137) and tool-specific codes
# (pytest returns 2 for interrupts, 5 for no-tests-collected) are preserved.
exit "${rc}"
