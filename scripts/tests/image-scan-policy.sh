#!/usr/bin/env bash
set -euo pipefail

scratch="$(mktemp -d)"
trap 'rm -rf "${scratch}"' EXIT

cat > "${scratch}/policy.json" <<'JSON'
{
  "version": 1,
  "issue": "cos-test",
  "groups": {
    "reviewed": {
      "owner": "cos-test",
      "expires": "2099-01-01",
      "reason": "Test acceptance"
    }
  }
}
JSON
printf 'debian\tHIGH\tCVE-TEST-1\tlibexample\t1.0\t-\treviewed\n' \
    > "${scratch}/baseline.tsv"
cat > "${scratch}/report.json" <<'JSON'
{
  "Results": [{
    "Target": "pr-validation:latest (debian 13.6)",
    "Type": "debian",
    "Vulnerabilities": [{
      "Severity": "HIGH",
      "VulnerabilityID": "CVE-TEST-1",
      "PkgName": "libexample",
      "InstalledVersion": "1.0"
    }]
  }]
}
JSON

check() {
    bash scripts/check-image-scan-policy.sh \
        "${scratch}/report.json" "${scratch}/baseline.tsv" "${scratch}/policy.json" \
        > "${scratch}/output" 2>&1
}
expect_fail() {
    if check; then
        printf 'Expected image policy to fail: %s\n' "$1" >&2
        exit 1
    fi
}

check # Existing, reviewed finding is accepted across PR/publish image names.

jq '.Results[0].Vulnerabilities[0].InstalledVersion = "1.1"' \
    "${scratch}/report.json" > "${scratch}/changed.json"
mv "${scratch}/changed.json" "${scratch}/report.json"
expect_fail 'changed installed version'

jq '.Results[0].Vulnerabilities[0].InstalledVersion = "1.0" |
    .Results[0].Vulnerabilities[0].FixedVersion = "1.2"' \
    "${scratch}/report.json" > "${scratch}/changed.json"
mv "${scratch}/changed.json" "${scratch}/report.json"
expect_fail 'vendor fix became available'

jq 'del(.Results[0].Vulnerabilities[0].FixedVersion) |
    .Results[0].Secrets = [{"Severity":"MEDIUM","RuleID":"test-secret"}]' \
    "${scratch}/report.json" > "${scratch}/changed.json"
mv "${scratch}/changed.json" "${scratch}/report.json"
expect_fail 'medium severity secret'
if ! grep -q 'test-secret' "${scratch}/output"; then
    printf 'Secret rule was not reported\n' >&2
    exit 1
fi

jq 'del(.Results[0].Secrets)' "${scratch}/report.json" \
    > "${scratch}/changed.json"
mv "${scratch}/changed.json" "${scratch}/report.json"
SCAN_POLICY_DATE=2100-01-01 expect_fail 'expired acceptance'

printf '{broken' > "${scratch}/report.json"
expect_fail 'malformed scanner report'

printf 'Image scan policy tests passed\n'
