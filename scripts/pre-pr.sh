#!/usr/bin/env bash
# Pre-PR quality gate runner.
# Invoked by `task pre-pr`; reads per-step timeouts from env vars.
# Runs steps sequentially and stops on first failure.
#
# Substeps use run_raw_task (QA_NO_WRAP=1) so that composite tasks like
# complexity and security:audit do not spawn nested durable wrappers.

set -euo pipefail

# run_raw_task: call a qa-task built-in with no log/status/timeout wrapper,
# preventing nested durable wrappers inside the pre-pr composite step.
run_raw_task() {
    QA_NO_WRAP=1 bash scripts/qa-task.sh "$@"
}
export -f run_raw_task

run_step() {
    local label="$1" deadline="$2"
    shift 2
    echo "pre-pr: ${label} (deadline ${deadline})"
    local tc="" rc=0
    if command -v gtimeout >/dev/null 2>&1; then
        tc="gtimeout"
    elif command -v timeout >/dev/null 2>&1; then
        tc="timeout"
    fi
    if [ -n "${tc}" ]; then
        # Use `bash -c '$0 "$@"'` so shell functions (like run_raw_task) can be
        # passed as the command — timeout cannot invoke shell functions directly.
        "${tc}" --foreground --kill-after=30s "${deadline}" \
            bash -c '$0 "$@"' "$@" || rc=$?
        if [ "${rc}" -eq 124 ] || [ "${rc}" -eq 137 ]; then
            echo "pre-pr: ${label} exceeded ${deadline} and was stopped" >&2
        fi
    else
        printf 'pre-pr: WARNING: gtimeout/timeout not found — running %s without a deadline\n' \
            "${label}" >&2
        bash -c '$0 "$@"' "$@" || rc=$?
    fi
    return "${rc}"
}

run_step "pre-commit"     "${PRE_PR_PRECOMMIT_TIMEOUT:-10m}" pre-commit run --all-files
run_step "lint"           "${PRE_PR_LINT_TIMEOUT:-10m}"      run_raw_task lint
run_step "typecheck"      "${PRE_PR_TYPECHECK_TIMEOUT:-10m}" run_raw_task typecheck
run_step "tests"          "${PRE_PR_TEST_TIMEOUT:-20m}"      run_raw_task test
run_step "complexity"     "${PRE_PR_COMPLEXITY_TIMEOUT:-10m}" run_raw_task complexity
run_step "docs build"     "${PRE_PR_DOCS_TIMEOUT:-10m}"       run_raw_task docs:build
run_step "security audit" "${PRE_PR_SECURITY_TIMEOUT:-10m}"  run_raw_task security:audit
