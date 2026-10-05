# Restricted Docker builder for the devcontainer

Evaluation for cos-2jj7, started 5 October 2026. **Status: options assessed, validation
not yet run.** `devcontainer.json` keeps `privileged: true` until one option passes
every check below on both the workstation and CI.

## Why privileged mode matters

The [host assessment](host-kernel-assessment.md) shows that privileged mode plus
passwordless sudo gives any process in the devcontainer root on the host kernel. On the
workstation that is the WSL2 VM, including its block devices, `/dev/kvm` and the Windows
user's files. Kernel advisories add no escape path only because escape needs no exploit.

## What the devcontainer needs Docker for

| Workflow                                  | Docker use                                                               |
| ----------------------------------------- | ------------------------------------------------------------------------ |
| `task test:mqtt`, `test:integration:full` | testcontainers starts `eclipse-mosquitto` and `testcontainers/ryuk`      |
| `task build:devcontainer`                 | builds the devcontainer image                                            |
| `task security:docker:scan`               | Trivy reads the local daemon's image through the Docker socket           |
| `task security:docker:lint`               | runs hadolint in a container                                             |
| `task build:wheel:probe` (cross)          | manylinux / musl cross images; QEMU for foreign `--platform` smoke tests |

`docker-init.sh` currently mounts `securityfs`, moves processes into a child cgroup to
enable cgroup v2 nesting, and starts a rootful `dockerd`. Each step needs privileges
that a default container lacks.

## Options

1. **Rootless Docker-in-Docker** (`dockerd-rootless.sh` with rootlesskit, user
   namespaces and fuse-overlayfs). Docker's own `docker:dind-rootless` image documents
   `--privileged`; known unprivileged setups instead need
   `--security-opt seccomp=unconfined`, `apparmor=unconfined`, `systempaths=unconfined`
   and `/dev/fuse`. That is far less than privileged mode, but still loosens isolation.
   Multi-arch QEMU (`binfmt_misc` registration) needs host root and would move to the
   host or CI runner.
2. **Sysbox runtime** on the host (`--runtime=sysbox-runc`). Runs a normal rootful
   `dockerd` inside an unprivileged container through user-namespace isolation. Needs
   Sysbox installed on every developer host and in every CI job before the container
   starts; support on WSL2 kernels is unverified.
3. **Reviewed remote builder** (`docker context` / `DOCKER_HOST` over SSH to a separate
   VM, or BuildKit remote driver). The devcontainer needs no privileges. The remote
   daemon is still fully controlled by whoever can reach it, so it must be a dedicated,
   disposable VM. testcontainers then starts Mosquitto remotely, so tests need the
   remote host's address (`TESTCONTAINERS_HOST_OVERRIDE`) and network reachability.
4. **Host Docker socket mount.** Rejected: it hands the devcontainer root-equivalent
   control of the host daemon, which is no improvement over privileged mode.

Recommendation: validate option 1 first, because it keeps the workflow self-contained
and runs on both WSL2 and GitHub-hosted runners. Fall back to option 3 if rootless
startup or testcontainers fails.

## Validation checklist (not yet run)

Each check must pass with `privileged` removed, on the workstation and in the
`devcontainer-build.yml` PR job:

1. Container startup: `post-start.sh` starts the daemon and `docker info` succeeds.
2. Nested build: `task build:devcontainer` completes.
3. Integration tests: `task test:integration:full` passes (testcontainers, Ryuk).
4. Image scan: `task security:docker:scan` reads the local image.
5. Lint: `task security:docker:lint` passes.
6. Cross builds: document which wheel targets or `--platform` smoke tests need the host.

Record results here, then change `devcontainer.json` in the same PR that adds the
evidence.
