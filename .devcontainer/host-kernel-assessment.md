# Host kernel assessment for the linux-libc-dev advisories

Recorded 5 October 2026 for cos-dam4 (owner issue cos-nxu6). The
[PR 499 review](security-review-2026-10-05.md) attributes 51 advisories to the
`linux-libc-dev 6.12.111-1` headers in the development image. Headers patch nothing: the
kernel that runs the devcontainer is the host's. This record gives, for each host, the
kernel, architecture, relevant modules and vendor fix status, and a verdict per
advisory. The image-scan baseline entries are unchanged.

Regenerate the inventory with `bash scripts/host-kernel-inventory.sh`, on the host or in
the devcontainer (which shares the host kernel). It reads the Kconfig symbols listed in
`scripts/kernel-advisory-subsystems.tsv`. The PR validation job of
`devcontainer-build.yml` prints the same inventory for the CI runner.

## Fixed-version source

Fixed versions come from the records of the Linux kernel CVE Numbering Authority
(`cveawg.mitre.org/api/cve/<id>`, fetched 5 October 2026). A stable kernel `6.18.N` is
fixed when the record lists `6.18.N' <= 6.18.*` as unaffected with `N' <= N`, or when
the mainline fix landed in 6.18 or earlier. The five pre-2024 advisories (CVE-2013-7445,
CVE-2019-19449, CVE-2019-19814, CVE-2021-3847, CVE-2021-3864) were not assigned by the
kernel's CVE authority and have no upstream fixed version; they are assessed by
configuration only.

## Hosts

| Host                       | Kernel                              | Arch   | Vendor base                                     |
| -------------------------- | ----------------------------------- | ------ | ----------------------------------------------- |
| Maintainer workstation     | `6.18.33.2-microsoft-standard-WSL2` | x86_64 | Microsoft WSL2 kernel on upstream 6.18.33       |
| GitHub runner ubuntu-24.04 | `6.17.0-1022-azure`                 | x86_64 | Ubuntu `linux-azure-6.17`, image 20260927.320.1 |

Only these two hosts run the privileged devcontainer. The macOS and Windows runners in
`rust-wheels.yml` build wheels without a Linux container. Other developers' hosts are
not inventoried. Before such a host runs the devcontainer, its owner runs the inventory
script and adds a column here; until then it is **open** (owner: cos-nxu6).

### Maintainer workstation (WSL2)

- Build `#1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026`, from `/proc/config.gz`.
- Loaded modules: autofs4, bridge, br_netfilter, ip_set, ip_tables, ipt/ip6t_REJECT,
  isofs, kvm, kvm_intel, nf_conntrack_netlink, nft_compat, sch_fq_codel, sunrpc, tun,
  xfrm_algo, xfrm_user and xt\_\* matches. No filesystem, Wi-Fi, GPU, SCTP, SMB or NFS
  module is loaded.
- Devices: `/dev/kvm`, `/dev/fuse`, `/dev/snd`, `/dev/vfio` and block devices
  `sda`–`sdf` are present; no `/dev/dri` (the GPU is paravirtualised through
  `/dev/dxg`), no `/dev/ipmi0`, no IOMMU groups.
- Settings: `fs.suid_dumpable=0`, `kernel.unprivileged_bpf_disabled=2`.
- No IPsec states are configured (`ip xfrm state` is empty).

### GitHub runner (ubuntu-24.04)

Each job runs on a fresh VM that is deleted afterwards, and the job user has
passwordless sudo. Code in a job, including the privileged devcontainer, already has
root on that VM, so a kernel bug grants it nothing more. GitHub patches the runner
kernel. Verdict for all 51 advisories: **not applicable** as a privilege boundary.

The CRITICAL advisory CVE-2026-43185 is also vendor-fixed: Ubuntu lists
`linux-azure-6.17` as fixed in `6.17.0-1021.21~24.04.1`
([tracker](https://ubuntu.com/security/CVE-2026-43185)); the runner runs `-1022`. The
Ubuntu tracker was not reliably reachable for the other 50, so no vendor status is
claimed for them.

## Privileged Docker-in-Docker exposure

When recorded, `devcontainer.json` set `privileged: true`, and the `vscode` user has
passwordless sudo. Together they gave any process in the devcontainer root on the host
kernel: all capabilities, every device (including the VM's block devices and
`/dev/kvm`), module loading and mounts. On the workstation, the host is the WSL2 VM,
which also mounts the Windows user's files. No kernel advisory added a container escape:
escape needed no exploit.

cos-2jj7 removes privileged mode: Docker now runs rootless in an unprivileged
devcontainer ([evaluation](restricted-builder-evaluation.md)). Root in the devcontainer
no longer has host capabilities or devices, so the kernel advisories that an
unprivileged process can reach matter again. They are listed after the table.

## Verdicts for the maintainer workstation

Verdicts: **fixed** (vendor build includes the upstream fix), **n/a** (affected code is
not built, has no device or is mitigated by a setting), **open** (affected and unfixed;
mitigation and owner given). Every open entry is owned by cos-nxu6 and is rechecked on
each WSL kernel update (`wsl --update`).

| #   | Advisory       | Subsystem            | Kconfig on host       | Verdict | Evidence                                                                                 |
| --- | -------------- | -------------------- | --------------------- | ------- | ---------------------------------------------------------------------------------------- |
| 8   | CVE-2026-43185 | ksmbd SMB Direct     | SMB_SERVER=n          | fixed   | Fixed in 6.18.16; ksmbd is not built either                                              |
| 9   | CVE-2013-7445  | DRM/GEM              | DRM=y                 | n/a     | No `/dev/dri` node; the GPU is reached through `/dev/dxg`                                |
| 10  | CVE-2019-19449 | F2FS                 | F2FS_FS=m             | open    | Needs a crafted F2FS image to be mounted (root only). Do not mount untrusted images      |
| 11  | CVE-2019-19814 | F2FS                 | F2FS_FS=m             | open    | As #10                                                                                   |
| 12  | CVE-2021-3847  | OverlayFS            | OVERLAY_FS=y          | open    | Local escalation, no upstream fix. No extra exposure while privileged (cos-2jj7)         |
| 13  | CVE-2021-3864  | SUID core dumps      | COREDUMP=y            | n/a     | `fs.suid_dumpable=0`: SUID processes write no core dumps                                 |
| 14  | CVE-2024-21803 | Bluetooth            | BT=m                  | fixed   | Affects kernels before 6.8 only                                                          |
| 15  | CVE-2024-58015 | ath12k               | ATH12K=n              | fixed   | Fixed in 6.14; driver not built                                                          |
| 16  | CVE-2025-38137 | PCI power control    | PCI_PWRCTRL=n         | fixed   | Fixed in 6.16; not built                                                                 |
| 17  | CVE-2025-38187 | nouveau              | DRM_NOUVEAU=m         | fixed   | Fixed in 6.16                                                                            |
| 18  | CVE-2025-38204 | JFS                  | JFS_FS=n              | fixed   | Fixed in 6.16; not built                                                                 |
| 19  | CVE-2025-38421 | AMD PMF              | AMD_PMF=n             | fixed   | Fixed in 6.16; not built                                                                 |
| 20  | CVE-2025-38636 | runtime verification | RV=n                  | fixed   | Fixed in 6.17; not built                                                                 |
| 21  | CVE-2025-39859 | OCP PTP clock        | PTP_1588_CLOCK_OCP=n  | fixed   | Fixed in 6.17; not built                                                                 |
| 22  | CVE-2025-39862 | mt76 Wi-Fi           | MT76_CORE=n           | fixed   | Fixed in 6.17; not built                                                                 |
| 23  | CVE-2025-39958 | s390 IOMMU           | S390=n                | fixed   | Fixed in 6.17; x86_64                                                                    |
| 24  | CVE-2025-40025 | F2FS                 | F2FS_FS=m             | fixed   | Fixed in 6.18                                                                            |
| 25  | CVE-2025-68174 | AMD KFD              | HSA_AMD=n             | fixed   | Fixed in 6.18; not built                                                                 |
| 26  | CVE-2025-68735 | Panthor GPU          | DRM_PANTHOR=n         | fixed   | Fixed in 6.18.2; not built                                                               |
| 31  | CVE-2026-23102 | arm64 SVE signals    | ARM64=n               | fixed   | Fixed in 6.18.8; x86_64                                                                  |
| 32  | CVE-2026-23208 | USB audio            | SND_USB_AUDIO=m       | fixed   | Fixed in 6.18.10                                                                         |
| 33  | CVE-2026-23327 | CXL mailbox          | CXL_BUS=n             | n/a     | Fixed only in 6.18.34; CXL is not built                                                  |
| 34  | CVE-2026-31493 | EFA RDMA             | INFINIBAND_EFA=n      | fixed   | Fixed in 6.18.21; not built                                                              |
| 35  | CVE-2026-31536 | ksmbd SMB Direct     | SMB_SERVER=n          | fixed   | Fixed in 6.18.11; not built                                                              |
| 36  | CVE-2026-31568 | s390 secure memory   | S390=n                | fixed   | Fixed in 6.18.21; x86_64                                                                 |
| 37  | CVE-2026-43263 | Wave5 codec          | VIDEO_WAVE_VPU=n      | fixed   | Fixed in 6.18.16; not built                                                              |
| 38  | CVE-2026-46130 | dm-verity FEC        | DM_VERITY_FEC=y       | open    | Fixed in 6.18.42. Needs a dm-verity target with FEC (root only). None are set up         |
| 39  | CVE-2026-46181 | mlx4 RDMA            | MLX4_INFINIBAND=m     | fixed   | Fixed in 6.18.30                                                                         |
| 40  | CVE-2026-46279 | allocation tags      | MEM_ALLOC_PROFILING=n | fixed   | Fixed in 6.18.27; not built                                                              |
| 41  | CVE-2026-52991 | PSI                  | PSI=y                 | fixed   | Fixed in 6.18.33                                                                         |
| 42  | CVE-2026-53000 | netfilter NAT        | NF_NAT=y              | fixed   | Fixed in 6.18.33                                                                         |
| 43  | CVE-2026-53091 | core network device  | NET=y                 | open    | Fixed in 7.0.10, no 6.18 backport listed. Wait for a WSL kernel with the fix             |
| 44  | CVE-2026-53109 | powerpc page tables  | PPC=n                 | fixed   | Fixed in 6.18.33; x86_64                                                                 |
| 45  | CVE-2026-53118 | vDPA                 | VDPA=n                | fixed   | Fixed in 6.18.33; not built                                                              |
| 46  | CVE-2026-53277 | arm64 KVM            | ARM64=n               | n/a     | Fixed only in 6.18.36; arm64 code, host is x86_64                                        |
| 47  | CVE-2026-53330 | AMD display          | DRM_AMD_DC=y          | n/a     | Fixed only in 6.18.36; no AMD GPU in the VM, amdgpu not loaded                           |
| 48  | CVE-2026-63879 | amdgpu HMM           | DRM_AMDGPU=m          | n/a     | No 6.18 fix; no AMD GPU in the VM, amdgpu not loaded                                     |
| 49  | CVE-2026-64283 | KVM guest_memfd      | KVM_GUEST_MEMFD=y     | open    | Fixed in 7.1.4 only. `/dev/kvm` is reachable from the privileged container; unused       |
| 50  | CVE-2026-68409 | mac80211             | MAC80211=m            | n/a     | Fixed only in 6.18.42; no wireless device in the VM, mac80211 not loaded                 |
| 51  | CVE-2026-68426 | IPsec device offload | XFRM_OFFLOAD=y        | open    | Fixed in 6.18.42. Needs an offloaded IPsec state (root only). None are configured        |
| 52  | CVE-2026-68470 | mac80211             | MAC80211=m            | n/a     | No 6.18 fix; no wireless device in the VM, mac80211 not loaded                           |
| 53  | CVE-2026-72042 | IPMI                 | IPMI_HANDLER=m        | n/a     | Fixed only in 6.18.40; no BMC, no `/dev/ipmi0`, module not loaded                        |
| 54  | CVE-2026-72098 | dm-verity FEC        | DM_VERITY_FEC=y       | open    | As #38                                                                                   |
| 55  | CVE-2026-72463 | IPsec input          | XFRM=y                | open    | 6.18.23–6.18.53 affected. Needs IPsec states (root only). None are configured            |
| 56  | CVE-2026-74269 | bnxt XDP             | BNXT=m                | n/a     | Fixed only in 6.18.53; no Broadcom NIC in the VM                                         |
| 57  | CVE-2026-74520 | IOMMU page faults    | IOMMU_IOPF=y          | n/a     | Fixed only in 6.18.44; the VM has no IOMMU groups                                        |
| 58  | CVE-2026-74752 | SCTP auth            | IP_SCTP=m             | open    | No 6.18 fix. Module autoloads on `socket()`; remote trigger needs an SCTP listener. None |
| 59  | CVE-2026-89631 | SMB client           | CIFS=m                | open    | Fixed in 6.18.51. Needs an SMB mount (root only) of a hostile server. Mount none         |
| 60  | CVE-2026-89633 | SMB1 client          | CIFS=m                | open    | As #59, and only for SMB1 mounts                                                         |
| 61  | CVE-2026-89638 | SMB client           | CIFS=m                | open    | As #59                                                                                   |
| 62  | CVE-2026-89675 | NFS server           | NFSD=m                | n/a     | Fixed only in 6.18.51; no NFS server runs, nfsd not loaded                               |

Totals: 26 fixed, 12 not applicable, 13 open.

Reachability from the unprivileged devcontainer (cos-2jj7):

- Not reachable: #10, #11, #38, #54 and #59–#61 need `CAP_SYS_ADMIN` in the host's
  initial user namespace (mounting F2FS or SMB, setting up dm-verity), which the
  devcontainer no longer has. #49 needs `/dev/kvm`, which is no longer passed in.
- Reachable: #12 (OverlayFS in a user namespace), #43 (network stack), #51 and #55
  (IPsec states can be configured in a network namespace owned by a user namespace,
  which rootless Docker creates) and #58 (SCTP sockets, if the module autoloads). These
  five wait for a WSL kernel with the upstream fixes.

## Follow-up

- Recheck the open entries when the WSL kernel moves past 6.18.51 or to 7.x, and before
  the header baseline entries expire on 27 October 2026.
- Add a column for every further host that runs the devcontainer.
- Prioritise the five entries still reachable from the unprivileged devcontainer.
