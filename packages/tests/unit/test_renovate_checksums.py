"""Unit tests for scripts/renovate_checksums.py — Renovate Dockerfile hash refresh.

Test Techniques Used:
- Specification-based: a bumped version gets the hashes of the downloaded assets
- Equivalence Partitioning: unchanged, bumped and untracked tools
- Decision Table: each verification source (asset digest, checksums file,
  release attestation, provenance) must agree, otherwise fail closed
- Error Guessing: malformed versions and missing SHA ARGs
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def rc() -> ModuleType:
    """Load scripts/renovate_checksums.py as a module."""
    path = Path(__file__).resolve().parents[3] / "scripts" / "renovate_checksums.py"
    spec = importlib.util.spec_from_file_location("renovate_checksums", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module
    spec.loader.exec_module(module)
    return module


BASE = """\
ARG OPENCODE_VERSION=v1.0.0
ARG OPENCODE_SHA256_AMD64=old-amd64
ARG OPENCODE_SHA256_ARM64=old-arm64
ARG BD_VERSION=v2.0.0
ARG BD_SHA256_AMD64=bd-amd64
ARG BD_SHA256_ARM64=bd-arm64
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeGh:
    """Serves one release per repo; records every call."""

    def __init__(self, assets: dict[str, bytes], *, fail: str | None = None) -> None:
        self.assets = assets
        self.digests = {name: f"sha256:{_sha(data)}" for name, data in assets.items()}
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.fail and self.fail in args:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="denied")
        if args[0] == "api":
            assets = [{"name": n, "digest": d} for n, d in self.digests.items()]
            return json.dumps({"assets": assets})
        if args[:2] == ["release", "download"]:
            out = Path(args[args.index("--dir") + 1])
            for i, arg in enumerate(args):
                if arg == "--pattern":
                    (out / args[i + 1]).write_bytes(self.assets[args[i + 1]])
        return ""


def _opencode_assets() -> dict[str, bytes]:
    return {"opencode-linux-x64.tar.gz": b"x64", "opencode-linux-arm64.tar.gz": b"a64"}


BD_AMD, BD_ARM = "beads_2.1.0_linux_amd64.tar.gz", "beads_2.1.0_linux_arm64.tar.gz"


def _beads_assets(listed_amd: str | None = None) -> dict[str, bytes]:
    """beads v2.1.0 assets; ``listed_amd`` overrides the checksums-file entry."""
    lines = f"{listed_amd or _sha(b'bd1')}  {BD_AMD}\n{_sha(b'bd2')}  {BD_ARM}\n"
    return {BD_AMD: b"bd1", BD_ARM: b"bd2", "checksums.txt": lines.encode()}


MISSING_SHA = BASE.replace("v1.0.0", "v1.1.0").replace(
    "ARG OPENCODE_SHA256_ARM64=old-arm64\n", ""
)


class TestUpdate:
    """update() refreshes only bumped tools and fails closed."""

    def test_bumped_tool_gets_verified_hashes(
        self, rc: ModuleType, tmp_path: Path
    ) -> None:
        """Specification-based: both arch ARGs take the downloaded assets' hashes."""
        # Arrange
        head = BASE.replace("v1.0.0", "v1.1.0")
        fake = FakeGh(_opencode_assets())

        # Act
        result, changed = rc.update(BASE, head, fake, tmp_path)

        # Assert
        assert f"ARG OPENCODE_SHA256_AMD64={_sha(b'x64')}\n" in result
        assert f"ARG OPENCODE_SHA256_ARM64={_sha(b'a64')}\n" in result
        assert "ARG BD_SHA256_AMD64=bd-amd64\n" in result
        assert changed == ["OPENCODE_VERSION=v1.1.0"]
        assert fake.calls[0] == ["api", "repos/anomalyco/opencode/releases/tags/v1.1.0"]
        assert sum(c[:2] == ["release", "verify-asset"] for c in fake.calls) == 2

    def test_unchanged_versions_make_no_calls(
        self, rc: ModuleType, tmp_path: Path
    ) -> None:
        """Equivalence Partitioning: no bump → no download, identical text."""
        fake = FakeGh({})

        result, changed = rc.update(BASE, BASE, fake, tmp_path)

        assert (result, changed, fake.calls) == (BASE, [], [])

    def test_provenance_and_checksums_file(
        self, rc: ModuleType, tmp_path: Path
    ) -> None:
        """Decision Table: beads needs the checksums file and SLSA provenance."""
        # Arrange
        head = BASE.replace("v2.0.0", "v2.1.0")
        fake = FakeGh(_beads_assets())

        # Act
        result, _ = rc.update(BASE, head, fake, tmp_path)

        # Assert
        assert f"ARG BD_SHA256_ARM64={_sha(b'bd2')}\n" in result
        verify = [c for c in fake.calls if c[:2] == ["attestation", "verify"]]
        assert len(verify) == 2
        assert "refs/tags/v2.1.0" in verify[0]
        assert "gastownhall/beads/.github/workflows/release.yml" in verify[0]

    @pytest.mark.parametrize(
        ("tamper", "message"),
        [
            ("digest", "release asset digest"),
            ("checksums", "checksums file"),
        ],
    )
    def test_disagreeing_sources_fail_closed(
        self, rc: ModuleType, tmp_path: Path, tamper: str, message: str
    ) -> None:
        """Decision Table: any disagreeing hash source aborts the update."""
        # Arrange
        head = BASE.replace("v2.0.0", "v2.1.0")
        listed = _sha(b"other") if tamper == "checksums" else None
        fake = FakeGh(_beads_assets(listed))
        if tamper == "digest":
            fake.digests[BD_AMD] = f"sha256:{_sha(b'swap')}"

        # Act / Assert
        with pytest.raises(rc.VerificationError, match=message):
            rc.update(BASE, head, fake, tmp_path)

    def test_failed_attestation_fails_closed(
        self, rc: ModuleType, tmp_path: Path
    ) -> None:
        """Error Guessing: a rejected release attestation propagates."""
        head = BASE.replace("v1.0.0", "v1.1.0")
        fake = FakeGh(_opencode_assets(), fail="verify-asset")

        with pytest.raises(subprocess.CalledProcessError):
            rc.update(BASE, head, fake, tmp_path)

    @pytest.mark.parametrize(
        ("head", "message"),
        [
            (BASE.replace("v1.0.0", "v1.1.0;rm"), "is not a version"),
            (MISSING_SHA, "missing"),
        ],
        ids=["injected-version", "missing-sha-arg"],
    )
    def test_rejects_malformed_input(
        self, rc: ModuleType, tmp_path: Path, head: str, message: str
    ) -> None:
        """Error Guessing: untrusted Dockerfile data is validated before use."""
        with pytest.raises(rc.VerificationError, match=message):
            rc.update(BASE, head, FakeGh(_opencode_assets()), tmp_path)


class TestMain:
    """main() writes only after successful verification."""

    def test_failure_leaves_file_untouched(
        self, rc: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error Guessing: exit 1 and no write when verification fails."""
        # Arrange
        base, head = tmp_path / "base", tmp_path / "head"
        base.write_text(BASE)
        head.write_text(BASE.replace("v1.0.0", "v1.1.0"))
        monkeypatch.setattr(rc, "gh", FakeGh(_opencode_assets(), fail="verify-asset"))

        # Act
        code = rc.main([str(base), str(head)])

        # Assert
        assert code == 1
        assert "v1.1.0" in head.read_text()
        assert "old-amd64" in head.read_text()

    def test_parse_checksums_accepts_binary_marker(self, rc: ModuleType) -> None:
        """Specification-based: sha256sum's ``*name`` binary marker is stripped."""
        text = f"{'a' * 64} *file.tar.gz\nnot a checksum line\n"

        assert rc.parse_checksums(text) == {"file.tar.gz": "a" * 64}
