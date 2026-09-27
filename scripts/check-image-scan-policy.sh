#!/usr/bin/env bash
# Compare Trivy findings with the reviewed, time-limited devcontainer baseline.
# The report may contain secret matches: never print or persist it outside its
# caller's private temporary file.
set -euo pipefail

report="${1:?usage: check-image-scan-policy.sh REPORT [BASELINE] [POLICY]}"
baseline="${2:-scripts/image-scan-baseline.tsv}"
policy="${3:-scripts/image-scan-acceptance.json}"
today="${SCAN_POLICY_DATE:-$(date -u +%F)}"

umask 077
scratch="$(mktemp -d)"
trap 'rm -rf "${scratch}"' EXIT

jq -e 'type == "object" and (.Results | type == "array")' \
    "${report}" >/dev/null
jq -e '.version == 1 and (.issue | type == "string") and
    (.groups | type == "object") and
    all(.groups[]; (.owner | type == "string") and
        (.reason | type == "string") and
        (.expires | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")))' \
    "${policy}" >/dev/null

jq -r '.groups | to_entries[] | [.key, .value.expires] | @tsv' \
    "${policy}" > "${scratch}/groups"
awk -F '\t' -v today="${today}" '
    FNR == NR { expiry[$1] = $2; next }
    /^#/ || NF == 0 { next }
    NF != 7 { print "image scan baseline: expected seven TSV fields" > "/dev/stderr"; bad = 1; next }
    !($7 in expiry) { print "image scan baseline: unknown group " $7 > "/dev/stderr"; bad = 1; next }
    expiry[$7] < today { print "image scan baseline: expired group " $7 > "/dev/stderr"; bad = 1; next }
    { OFS = "\t"; print $1, $2, $3, $4, $5, $6 }
    END { if (bad) exit 1 }
' "${scratch}/groups" "${baseline}" | LC_ALL=C sort -u > "${scratch}/allowed"

# Normalize the OS target: PR and publish builds use different image names.
# Include severity, installed version, and fixed version in each fingerprint so
# a changed advisory, upgraded package, or newly available fix requires review.
jq -r '
    .Results[]? as $result | $result.Vulnerabilities[]? |
    select(.Severity == "HIGH" or .Severity == "CRITICAL") |
    [(if $result.Type == "debian" then "debian" else $result.Target end),
     .Severity, .VulnerabilityID, .PkgName, .InstalledVersion,
     (if (.FixedVersion // "") == "" then "-" else .FixedVersion end)] | @tsv
' "${report}" | LC_ALL=C sort -u > "${scratch}/current"

secret_count="$(jq '[.Results[]?.Secrets[]?] | length' "${report}")"
LC_ALL=C comm -23 "${scratch}/current" "${scratch}/allowed" > "${scratch}/unreviewed"
finding_count="$(wc -l < "${scratch}/current")"
unreviewed_count="$(wc -l < "${scratch}/unreviewed")"
printf 'Image scan: %s HIGH/CRITICAL findings, %s unreviewed, %s secrets\n' \
    "${finding_count}" "${unreviewed_count}" "${secret_count}"

if [ "${unreviewed_count}" -gt 0 ]; then
    printf 'Unreviewed findings: target | severity | ID | package | installed | fixed\n' >&2
    awk -F '\t' '{ OFS=" | "; print $1, $2, $3, $4, $5, $6 }' \
        "${scratch}/unreviewed" >&2
fi
if [ "${secret_count}" -gt 0 ]; then
    printf 'Secret findings: target | severity | rule (match values withheld)\n' >&2
    jq -r '.Results[]? | .Target as $target | .Secrets[]? |
        [$target, .Severity, .RuleID] | join(" | ")' "${report}" >&2
fi
if [ "${unreviewed_count}" -gt 0 ] || [ "${secret_count}" -gt 0 ]; then
    exit 1
fi
