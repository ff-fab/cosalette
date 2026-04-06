#!/usr/bin/env bash
# quality-summary.sh — Run lint, typecheck, and unit tests, then output a structured JSON summary.
#
# Usage: bash scripts/quality-summary.sh
#
# All progress messages go to stderr; only JSON goes to stdout.
# Exit code: 0 if all checks pass, 1 if any fail.

set -uo pipefail

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
if ! command -v jq &>/dev/null; then
  echo "ERROR: jq is required but not found on PATH" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
truncate_details() {
  # Keep last ~20 lines, truncated to 500 chars
  local output="$1"
  local tail_lines
  tail_lines=$(printf '%s' "$output" | tail -n 20)
  if [ ${#tail_lines} -gt 500 ]; then
    printf '%s' "$tail_lines" | tail -c 500
  else
    printf '%s' "$tail_lines"
  fi
}

# ---------------------------------------------------------------------------
# Run checks — always run all, never fail-fast
# ---------------------------------------------------------------------------
overall_ok=true

# --- Lint ---
echo "Running lint..." >&2
lint_output=$(task lint 2>&1)
lint_rc=$?
lint_passed=true
lint_details=""
if [ "$lint_rc" -ne 0 ]; then
  lint_passed=false
  overall_ok=false
  lint_details=$(truncate_details "$lint_output")
fi
echo "  lint: exit $lint_rc" >&2

# --- Typecheck ---
echo "Running typecheck..." >&2
typecheck_output=$(task typecheck 2>&1)
typecheck_rc=$?
typecheck_passed=true
typecheck_details=""
if [ "$typecheck_rc" -ne 0 ]; then
  typecheck_passed=false
  overall_ok=false
  typecheck_details=$(truncate_details "$typecheck_output")
fi
echo "  typecheck: exit $typecheck_rc" >&2

# --- Tests ---
echo "Running tests..." >&2
test_output=$(task test:unit 2>&1)
test_rc=$?
test_passed=true
test_details=""
if [ "$test_rc" -ne 0 ]; then
  test_passed=false
  overall_ok=false
  test_details=$(truncate_details "$test_output")
fi
echo "  tests: exit $test_rc" >&2

# Extract pytest counts from the summary line.
# Typical line: "== 142 passed, 3 skipped in 5.23s =="
# or: "== 1 failed, 141 passed, 3 skipped in 5.30s =="
extract_count() {
  local pattern="$1"
  local text="$2"
  local val
  # Anchor to pytest summary line (== ... ==) to avoid matching stray occurrences
  val=$(printf '%s' "$text" | grep -E '=+.*=+' | grep -oE "[0-9]+ ${pattern}" | grep -oE '[0-9]+' | head -n1)
  printf '%s' "${val:-0}"
}

test_total=0
test_passed_count=$(extract_count "passed" "$test_output")
test_failed_count=$(extract_count "failed" "$test_output")
test_errors=$(extract_count "error" "$test_output")
test_skipped=$(extract_count "skipped" "$test_output")
test_total=$((test_passed_count + test_failed_count + test_errors + test_skipped))

# ---------------------------------------------------------------------------
# Determine overall
# ---------------------------------------------------------------------------
if $overall_ok; then
  overall="PASS"
else
  overall="FAIL"
fi

timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# Assemble JSON with jq (safe escaping of arbitrary details strings)
# ---------------------------------------------------------------------------
jq -n \
  --argjson lint_passed "$lint_passed" \
  --argjson lint_rc "$lint_rc" \
  --arg lint_details "$lint_details" \
  --argjson typecheck_passed "$typecheck_passed" \
  --argjson typecheck_rc "$typecheck_rc" \
  --arg typecheck_details "$typecheck_details" \
  --argjson test_passed "$test_passed" \
  --argjson test_rc "$test_rc" \
  --argjson test_total "$test_total" \
  --argjson test_passed_count "$test_passed_count" \
  --argjson test_failed_count "$test_failed_count" \
  --argjson test_errors "$test_errors" \
  --argjson test_skipped "$test_skipped" \
  --arg test_details "$test_details" \
  --arg overall "$overall" \
  --arg timestamp "$timestamp" \
  '{
    lint: {passed: $lint_passed, exit_code: $lint_rc, details: $lint_details},
    typecheck: {passed: $typecheck_passed, exit_code: $typecheck_rc, details: $typecheck_details},
    tests: {passed: $test_passed, exit_code: $test_rc, total: $test_total, passed_count: $test_passed_count, failed_count: $test_failed_count, errors: $test_errors, skipped: $test_skipped, details: $test_details},
    overall: $overall,
    timestamp: $timestamp
  }'

# ---------------------------------------------------------------------------
# Exit
# ---------------------------------------------------------------------------
if [ "$overall" = "PASS" ]; then
  exit 0
else
  exit 1
fi
