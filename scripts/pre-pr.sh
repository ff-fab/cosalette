#!/usr/bin/env bash
# Pre-PR quality gate runner.
# Invoked by `task pre-pr`; reads per-step timeouts from env vars.
# Runs steps sequentially and stops on first failure.

set -euo pipefail

_timeout_cmd() {
    if command -v gtimeout >/dev/null 2>&1; then
        printf 'gtimeout'
    else
        printf 'timeout'
    fi
}

run_step() {
    local label="$1" deadline="$2"
    shift 2
    echo "pre-pr: ${label} (deadline ${deadline})"
    local tc rc=0
    tc=$(_timeout_cmd)
    "$tc" --foreground --kill-after=30s "${deadline}" "$@" || rc=$?
    if [ "${rc}" -eq 124 ] || [ "${rc}" -eq 137 ]; then
        echo "pre-pr: ${label} exceeded ${deadline} and was stopped" >&2
    fi
    return "${rc}"
}

run_step "pre-commit"     "${PRE_PR_PRECOMMIT_TIMEOUT:-10m}" pre-commit run --all-files
run_step "lint"           "${PRE_PR_LINT_TIMEOUT:-10m}"      task lint
run_step "typecheck"      "${PRE_PR_TYPECHECK_TIMEOUT:-10m}" task typecheck
run_step "tests"          "${PRE_PR_TEST_TIMEOUT:-20m}"      task test
run_step "complexity"     "${PRE_PR_COMPLEXITY_TIMEOUT:-10m}" task complexity
run_step "security audit" "${PRE_PR_SECURITY_TIMEOUT:-10m}"  task security:audit
