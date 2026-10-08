# Restricted Docker builder for the devcontainer

Evaluation and decision for cos-2jj7, 5 October 2026. **Outcome: the devcontainer no
longer runs privileged.** Docker runs rootless inside it (option 1 below). This record
lists the options, the configuration that was validated and the evidence.

## Why privileged mode mattered

The [host assessment](host-kernel-assessment.md) shows that privileged mode plus
passwordless sudo gave any process in the devcontainer root on the host kernel. On the
workstation that is the WSL2 VM, including its block devices, `/dev/kvm` and the Windows
user's files. Escape needed no kernel exploit.

## What the devcontainer needs Docker for

| Workflow                                  | Docker use                                                          |
| ----------------------------------------- | ------------------------------------------------------------------- |
| `task test:mqtt`, `test:integration:full` | testcontainers starts `eclipse-mosquitto` and `testcontainers/ryuk` |
| `task build:devcontainer`                 | builds the devcontainer image                                       |
| `task security:docker:scan`               | Trivy reads the local daemon's image through the Docker socket      |
| `task security:docker:lint`               | runs hadolint in a container                                        |
| foreign `--platform` smoke tests          | QEMU through the host kernel's `binfmt_misc` registrations          |

## Options

1. **Rootless Docker-in-Docker** (chosen). `dockerd-rootless.sh` runs the daemon in a
   user namespace mapped to the `vscode` user's subordinate IDs (`/etc/subuid`), with
   slirp4netns networking. The devcontainer gets no added capabilities and no host
   devices except `/dev/net/tun`.
2. **Sysbox runtime** on the host. Needs Sysbox installed on every developer host and in
   every CI job before the container starts; not needed once option 1 works.
3. **Reviewed remote builder** over SSH or a BuildKit remote driver. Needs a dedicated
   disposable VM and network setup for testcontainers; kept as the fallback.
4. **Host Docker socket mount.** Rejected: it gives root-equivalent control of the host
   daemon, which is no improvement over privileged mode.

## Validated configuration

`devcontainer.json` drops `privileged` and sets:

- `--security-opt=seccomp=unconfined`: Docker's default seccomp profile forbids creating
  user namespaces without `CAP_SYS_ADMIN`.
- `--security-opt=apparmor=unconfined`: the `docker-default` AppArmor profile forbids
  the mounts rootlesskit makes (no effect on WSL2, which has no AppArmor).
- `--security-opt=systempaths=unconfined`: rootlesskit mounts a fresh `/proc` in its
  namespaces, which masked `/proc` paths prevent.
- `--device=/dev/net/tun` for slirp4netns.
- A named volume at `/home/vscode/.local/share/docker` (the rootless data root), because
  overlayfs on the container's own overlay root fails with `EINVAL`.
- `DOCKER_HOST=unix:///run/user/vscode/docker.sock` (in `containerEnv`) and
  `XDG_RUNTIME_DIR=/run/user/vscode` use a stable user-named path. `XDG_RUNTIME_DIR` is
  set only by `docker-runtime.sh` for the daemon, not in `containerEnv`: VS Code Server
  starts before `post-start.sh` creates the directory and puts its own sockets in
  `$XDG_RUNTIME_DIR`, so a container-wide value breaks the VS Code connection.
- `--tmpfs=/run/user:mode=0755` supplies a root-owned parent. Startup creates its
  private `vscode` subdirectory with mode 0700 and the actual `id -u`/`id -g`,
  preserving Dev Containers host UID remapping. It also corrects the named data-volume
  root ownership copied from the image. The daemon leaves its sockets and state there
  owned by subordinate IDs, which `vscode` cannot remove, so a restarted container could
  not clean it up. The tmpfs starts empty on every container start; `docker-init.sh`
  fails with a clear message if it finds state of a previous daemon.
- `TESTCONTAINERS_HOST_OVERRIDE=localhost` and
  `TESTCONTAINERS_CONNECTION_MODE=docker_host`. testcontainers otherwise detects
  Docker-in-Docker and connects to the bridge gateway, which rootless mode keeps in its
  own network namespace. rootlesskit binds published ports on the devcontainer's
  loopback instead.

The image installs `uidmap` and `slirp4netns`. It downloads the
`docker-ce-rootless-extras` Debian package pinned to `DOCKER_VERSION` using apt's signed
repository metadata and extracts only its two daemon launcher/setup scripts. It does not
install that package: its `dbus-user-session` dependency installs systemd, although this
container starts the daemon directly without a service manager. RootlessKit's three
binaries are installed separately from the checksum-pinned
[upstream v3.2.0 release](https://github.com/rootless-containers/rootlesskit/releases/tag/v3.2.0).
The inspected amd64 binary reports Go 1.26.8, `golang.org/x/net v0.58.0` and
`golang.org/x/crypto v0.57.0`, replacing Docker's vulnerable bundled module versions.
Debian's setuid `newuidmap`/`newgidmap` fail with `EPERM` writing the namespace map in
an unprivileged container; the image replaces the setuid bit with the
`cap_setuid`/`cap_setgid` file capabilities. Docker's own `docker:dind-rootless` image
also ships them without the setuid bit.

`/dev/fuse` is not needed: the rootless daemon uses native overlayfs in the user
namespace.

## Evidence

Tested on the workstation (WSL2 6.18.33.2) in a container without `--privileged`,
started by the existing Docker 29.8.1 daemon:

| Check                                  | Result                                                  |
| -------------------------------------- | ------------------------------------------------------- |
| Baseline: rootful `docker-init.sh`     | Fails without privileges (iptables `Permission denied`) |
| Rootless daemon start                  | Pass, `docker info` reports `name=rootless`, no cgroups |
| Nested `docker run` and `docker build` | Pass                                                    |
| `task test:mqtt`                       | 13 passed (with the testcontainers settings above)      |
| `task test:integration:full`           | 101 + 13 passed                                         |
| `task security:docker:lint`            | Pass                                                    |
| `task build:devcontainer` (nested)     | Pass (exit 0, inside a rootless test container)         |
| `task security:docker:scan`            | Not run locally; verified by the CI scan (see below)    |

CI: the PR job of `devcontainer-build.yml` starts the devcontainer from
`devcontainer.json` (post-start starts the rootless daemon) and runs
`.devcontainer/tests/rootless-docker.sh`. It fails if the container is privileged or the
daemon is not rootless, and builds an image and connects to a published port. The same
job then runs `security:docker:scan` (Trivy) on the built image and uploads the review
report; that scan is the evidence for the image's findings and secrets. It runs against
the runner's rootful daemon, so the scan through the rootless socket (`DOCKER_HOST` in
`scripts/qa-task.sh`) is not exercised by it and is not yet verified.

## Limits

- Without systemd, the rootless daemon has no cgroup controller: `--memory`, `--cpus`
  and similar limits on nested containers are not enforced.
- Registering QEMU (`tonistiigi/binfmt --install`) needs host root. Foreign `--platform`
  runs work only if the host kernel already has the registrations (Docker Desktop and CI
  set them up); otherwise run them in CI.
- slirp4netns networking is slower than a bridge for large image pulls.
- Code in the devcontainer still has passwordless sudo, but that is now root of an
  unprivileged container, not of the host kernel. Kernel advisories that do not need
  root (see the host assessment) matter again.
