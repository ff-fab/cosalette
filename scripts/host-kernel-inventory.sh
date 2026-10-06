#!/usr/bin/env bash
# Print the host-kernel inventory for the kernel advisories reviewed in PR 499.
#
# Usage: bash scripts/host-kernel-inventory.sh
#
# The linux-libc-dev headers in the development image do not patch or expose
# the kernel; the devcontainer shares the host kernel.
# This prints what .devcontainer/host-kernel-assessment.md records per host:
# kernel release and build, architecture, the Kconfig state of each affected
# subsystem (scripts/kernel-advisory-subsystems.tsv), loaded modules, device
# nodes and mitigating sysctls. It only reads /proc, /sys, /dev and /boot; it
# works on the host and inside the devcontainer, which shares the host kernel.
set -euo pipefail

table="$(dirname "$0")/kernel-advisory-subsystems.tsv"

kernel_config() {
  if [[ -r /proc/config.gz ]]; then
    zcat /proc/config.gz
  elif [[ -r "/boot/config-$(uname -r)" ]]; then
    cat "/boot/config-$(uname -r)"
  else
    return 1
  fi
}

echo "== Kernel"
echo "release:      $(uname -r)"
echo "build:        $(uname -v)"
echo "architecture: $(uname -m)"
if [[ -r /etc/os-release ]]; then
  echo "userspace:    $(. /etc/os-release && echo "${PRETTY_NAME:-unknown}")"
fi

echo
echo "== Mitigating settings"
for setting in fs/suid_dumpable kernel/core_pattern kernel/modules_disabled \
  kernel/unprivileged_bpf_disabled user/max_user_namespaces; do
  value="$(cat "/proc/sys/$setting" 2>/dev/null || echo unavailable)"
  printf '%-32s %s\n' "$setting" "$value"
done

echo
echo "== Device nodes"
for node in /dev/dri /dev/kvm /dev/fuse /dev/ipmi0 /dev/snd /dev/vfio; do
  if [[ -e "$node" ]]; then echo "present: $node"; else echo "absent:  $node"; fi
done
echo "iommu groups: $(find /sys/kernel/iommu_groups -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"

echo
echo "== Loaded modules"
cut -d' ' -f1 /proc/modules 2>/dev/null | sort | tr '\n' ' '
echo

echo
echo "== Advisory subsystems (y = built in, m = module, n = not built)"
config=""
if ! config="$(kernel_config)"; then
  echo "kernel config unavailable: no /proc/config.gz or /boot/config-$(uname -r)"
  exit 0
fi
while IFS=$'\t' read -r advisory symbols subsystem; do
  [[ -z "$advisory" || "$advisory" == \#* ]] && continue
  states=()
  IFS=, read -ra names <<<"$symbols"
  for name in "${names[@]}"; do
    state="$(grep -E "^CONFIG_${name}=" <<<"$config" | cut -d= -f2 || true)"
    states+=("${name}=${state:-n}")
  done
  printf '%-16s %-26s %s\n' "$advisory" "$subsystem" "${states[*]}"
done <"$table"
