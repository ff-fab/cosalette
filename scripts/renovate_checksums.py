#!/usr/bin/env python3
"""Refresh the SHA-256 ARGs of Renovate-bumped tools in the devcontainer Dockerfile.

Usage: renovate_checksums.py BASE_DOCKERFILE HEAD_DOCKERFILE

HEAD_DOCKERFILE (a Renovate branch's Dockerfile, read as data) is rewritten in
place. For every tool in ``TOOLS`` whose ``<PREFIX>_VERSION`` ARG differs from
BASE_DOCKERFILE (the default branch), the release assets are downloaded with
``gh`` and their SHA-256 must agree with the GitHub release asset digest, the
publisher's checksums file or per-asset ``.sha256`` sidecar (when one exists)
and the publisher's attestation (when one exists). Any disagreement or failed
verification exits non-zero without writing anything (ADR-090).

Standard library only, so the workflow runs it with the runner's ``python3 -I``.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ARG_RE = re.compile(r"^ARG ([A-Z0-9_]+)=(\S*)$", re.MULTILINE)
VERSION_RE = re.compile(r"v?\d+(?:\.\d+){1,3}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class Tool:
    """How one release is located and verified.

    Asset and tag templates use ``{v}`` for the version without a leading ``v``.
    """

    repo: str
    assets: dict[str, str]
    tag: str = "v{v}"
    checksums: str | None = None
    #: Each asset has a ``<asset>.sha256`` sidecar holding its bare hash.
    sidecar: bool = False
    #: The release is immutable and carries GitHub's release attestation.
    release_attestation: bool = False
    #: Workflow that signs the SLSA provenance (``gh attestation verify``).
    signer_workflow: str | None = None


TOOLS: dict[str, Tool] = {
    "SYFT": Tool(
        "anchore/syft",
        {
            "AMD64": "syft_{v}_linux_amd64.tar.gz",
            "ARM64": "syft_{v}_linux_arm64.tar.gz",
        },
        checksums="syft_{v}_checksums.txt",
        release_attestation=True,
    ),
    "TASK": Tool(
        "go-task/task",
        {"AMD64": "task_linux_amd64.tar.gz", "ARM64": "task_linux_arm64.tar.gz"},
        checksums="task_checksums.txt",
    ),
    "BD": Tool(
        "gastownhall/beads",
        {
            "AMD64": "beads_{v}_linux_amd64.tar.gz",
            "ARM64": "beads_{v}_linux_arm64.tar.gz",
        },
        checksums="checksums.txt",
        signer_workflow="gastownhall/beads/.github/workflows/release.yml",
    ),
    "ROOTLESSKIT": Tool(
        "rootless-containers/rootlesskit",
        {"AMD64": "rootlesskit-x86_64.tar.gz", "ARM64": "rootlesskit-aarch64.tar.gz"},
        checksums="SHA256SUMS",
        release_attestation=True,
    ),
    "CARGO_DENY": Tool(
        "EmbarkStudios/cargo-deny",
        {
            "AMD64": "cargo-deny-{v}-x86_64-unknown-linux-musl.tar.gz",
            "ARM64": "cargo-deny-{v}-aarch64-unknown-linux-musl.tar.gz",
        },
        tag="{v}",
        sidecar=True,
    ),
    "OPENCODE": Tool(
        "anomalyco/opencode",
        {"AMD64": "opencode-linux-x64.tar.gz", "ARM64": "opencode-linux-arm64.tar.gz"},
        release_attestation=True,
    ),
}


class VerificationError(Exception):
    """A release asset could not be verified; nothing is written."""


def gh(args: list[str]) -> str:
    """Run ``gh`` and return stdout; a non-zero exit raises."""
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def parse_args(text: str) -> dict[str, str]:
    """Return the ``ARG NAME=value`` assignments of a Dockerfile."""
    return dict(ARG_RE.findall(text))


def parse_checksums(text: str) -> dict[str, str]:
    """Parse ``<sha256>  <name>`` lines (sha256sum format, optional ``*``)."""
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and SHA256_RE.fullmatch(parts[0]):
            result[parts[1].lstrip("*")] = parts[0]
    return result


def release_hashes(
    tool: Tool, version: str, run: Runner, workdir: Path
) -> dict[str, str]:
    """Download, cross-check and verify one release; return ``{arch: sha256}``."""
    v = version.removeprefix("v")
    tag = tool.tag.format(v=v)
    names = {arch: tmpl.format(v=v) for arch, tmpl in tool.assets.items()}
    release = json.loads(run(["api", f"repos/{tool.repo}/releases/tags/{tag}"]))
    api_digests = {a["name"]: a.get("digest") or "" for a in release["assets"]}
    wanted = list(names.values())
    wanted += [f"{name}.sha256" for name in wanted] if tool.sidecar else []
    wanted += [tool.checksums.format(v=v)] if tool.checksums else []
    patterns = [arg for name in wanted for arg in ("--pattern", name)]
    run(
        [
            "release",
            "download",
            tag,
            "--repo",
            tool.repo,
            "--dir",
            str(workdir),
            *patterns,
        ]
    )
    published = (
        parse_checksums((workdir / tool.checksums.format(v=v)).read_text())
        if tool.checksums
        else {}
    )

    hashes = {}
    for arch, name in names.items():
        path = workdir / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if api_digests.get(name) != f"sha256:{actual}":
            raise VerificationError(f"{name}: release asset digest does not match")
        if tool.checksums and published.get(name) != actual:
            raise VerificationError(f"{name}: checksums file does not match")
        sidecar = workdir / f"{name}.sha256"
        if tool.sidecar and sidecar.read_text().split() != [actual]:
            raise VerificationError(f"{name}: .sha256 sidecar does not match")
        if tool.release_attestation:
            run(["release", "verify-asset", tag, str(path), "--repo", tool.repo])
        if tool.signer_workflow:
            run([
                "attestation", "verify", str(path),
                "--repo", tool.repo,
                "--signer-workflow", tool.signer_workflow,
                "--source-ref", f"refs/tags/{tag}",
                "--deny-self-hosted-runners",
            ])  # fmt: skip
        hashes[arch] = actual
    return hashes


def update(base: str, head: str, run: Runner, workdir: Path) -> tuple[str, list[str]]:
    """Return ``head`` with refreshed hashes and the list of updated tools."""
    base_args, head_args = parse_args(base), parse_args(head)
    changed = []
    for prefix, tool in TOOLS.items():
        version = head_args.get(f"{prefix}_VERSION")
        if version is None or version == base_args.get(f"{prefix}_VERSION"):
            continue
        if not VERSION_RE.fullmatch(version):
            raise VerificationError(f"{prefix}_VERSION={version!r} is not a version")
        tool_dir = workdir / prefix.lower()
        tool_dir.mkdir()
        for arch, sha in release_hashes(tool, version, run, tool_dir).items():
            name = f"{prefix}_SHA256_{arch}"
            if name not in head_args:
                raise VerificationError(f"ARG {name} is missing")
            head = head.replace(
                f"ARG {name}={head_args[name]}\n", f"ARG {name}={sha}\n"
            )
        changed.append(f"{prefix}_VERSION={version}")
    return head, changed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    base, head = (Path(p) for p in argv)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            text, changed = update(base.read_text(), head.read_text(), gh, Path(tmp))
    except (VerificationError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or ""
        print(f"verification failed: {exc} {detail}".rstrip(), file=sys.stderr)
        return 1
    head.write_text(text)
    for line in changed:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
