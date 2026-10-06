#!/usr/bin/env bash
# Linux exposes the highest supported capability bit, including on new kernels.
full_capability_mask() {
    local last_cap="$1"
    if [[ ! "$last_cap" =~ ^[0-9]+$ ]] || (( last_cap > 63 )); then
        echo "unsupported cap_last_cap: $last_cap" >&2
        return 1
    fi
    if (( last_cap == 63 )); then
        printf '%016x' -1
    else
        printf '%016x' "$(( (1 << (last_cap + 1)) - 1 ))"
    fi
}
