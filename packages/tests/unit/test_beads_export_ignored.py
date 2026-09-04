"""Guard tests for the .beads/issues.jsonl ignore rules (F-SC1 / CWE-359).

``bd export`` / ``task beads:sync`` writes ``.beads/issues.jsonl``, a local-only
snapshot that embeds a maintainer's personal email in ~655 places. The 2026-08
security audit (finding F-SC1) made it gitignored and untracked. Two independent
ignore rules keep it that way, on purpose:

1. the root ``.gitignore`` — unanchored ``issues.jsonl``
2. ``.beads/.gitignore`` — anchored ``/issues.jsonl``

Either alone suffices, so removing one must not re-expose the export. These
guards turn a careless edit to either file into a CI failure instead of a silent
loss of the control.

Test Techniques Used:
- Specification-based: each layer's exact pattern is part of the F-SC1 contract.
- Decision Table: layer 1 present / layer 2 present → the file stays ignored;
  each layer is asserted independently so a failure names the one that went.
- Error Guessing: anchoring regression (``issues.jsonl`` instead of
  ``/issues.jsonl``), and the export being re-tracked by ``git add``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Depth breakdown: unit/ → tests/ → packages/ → <project root>
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ROOT_GITIGNORE = _PROJECT_ROOT / ".gitignore"
_BEADS_GITIGNORE = _PROJECT_ROOT / ".beads" / ".gitignore"
_EXPORT_PATH = ".beads/issues.jsonl"
# The Dolt database and its .darc backup blobs: ~32 MB of binary chunks and the
# actual source of truth for issue data. Committing these is the higher-consequence
# accident, so each carries the same two-layer treatment as the export.
_DB_PATHS = (".beads/dolt/", ".beads/backup/")


def _rules(gitignore: Path) -> set[str]:
    """Return the active (non-comment, non-blank) patterns in *gitignore*."""
    lines = gitignore.read_text(encoding="utf-8").splitlines()
    return {
        stripped
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    }


class TestBeadsExportIgnoreLayers:
    """Both ignore layers for .beads/issues.jsonl must stay in place."""

    def test_root_gitignore_ignores_the_export(self) -> None:
        """Layer 1: the root .gitignore carries the unanchored rule.

        Technique: Specification-based — the pattern is unanchored so it also
        covers exports written anywhere else in the tree.
        """
        # Arrange / Act
        rules = _rules(_ROOT_GITIGNORE)

        # Assert
        assert "issues.jsonl" in rules, (
            "Root .gitignore lost its `issues.jsonl` rule — layer 1 of the F-SC1"
            " control. Restore it; do not rely on .beads/.gitignore alone."
        )

    def test_beads_gitignore_ignores_the_export_independently(self) -> None:
        """Layer 2: .beads/.gitignore carries its own anchored rule.

        Technique: Specification-based — this layer is deliberately redundant so
        that deleting the root rule cannot silently re-expose the export.
        """
        # Arrange / Act
        rules = _rules(_BEADS_GITIGNORE)

        # Assert
        assert "/issues.jsonl" in rules, (
            "`.beads/.gitignore` lost its `/issues.jsonl` rule — layer 2 of the"
            " F-SC1 control. Both layers must stay."
        )

    def test_beads_rule_is_anchored_to_the_beads_directory(self) -> None:
        """The .beads rule matches only .beads/issues.jsonl, not nested paths.

        Technique: Error Guessing — an unanchored copy would over-match every
        nested issues.jsonl under .beads/, including Dolt working data.
        """
        # Arrange / Act
        rules = _rules(_BEADS_GITIGNORE)

        # Assert
        assert "issues.jsonl" not in rules, (
            "`.beads/.gitignore` must use the anchored `/issues.jsonl`, not the"
            " unanchored form."
        )


class TestBeadsDatabaseIgnoreLayers:
    """The Dolt DB and its backup blobs must also carry two ignore layers.

    Until this suite was extended, ``.beads/dolt/`` and ``.beads/backup/`` were
    covered by ``.beads/.gitignore`` alone while the far less sensitive JSONL
    export had two layers plus these guards — the protection was strongest on the
    cheaper asset. These tests close that asymmetry.
    """

    @pytest.mark.parametrize("path", _DB_PATHS)
    def test_root_gitignore_ignores_the_database(self, path: str) -> None:
        """Layer 1: the root .gitignore names the DB and backup directories.

        Technique: Specification-based — the root layer must stand alone if
        ``.beads/.gitignore`` is ever deleted along with the directory it guards.
        """
        # Arrange / Act
        rules = _rules(_ROOT_GITIGNORE)

        # Assert
        assert path in rules, (
            f"Root .gitignore lost its `{path}` rule — layer 1 protecting the Dolt"
            " database. Without it, deleting .beads/.gitignore would let"
            " `git add -A` stage ~32 MB of database chunks."
        )

    @pytest.mark.parametrize("rule", ("dolt/", "backup/"))
    def test_beads_gitignore_ignores_the_database_independently(
        self, rule: str
    ) -> None:
        """Layer 2: .beads/.gitignore carries its own relative rules.

        Technique: Decision Table — either layer alone must suffice, so each is
        asserted separately and a failure names the layer that went.
        """
        # Arrange / Act
        rules = _rules(_BEADS_GITIGNORE)

        # Assert
        assert rule in rules, (
            f"`.beads/.gitignore` lost its `{rule}` rule — layer 2 protecting the"
            " Dolt database. Both layers must stay."
        )


class TestBeadsExportUntracked:
    """The export must never be tracked by git."""

    def test_export_is_not_tracked_by_git(self) -> None:
        """`git ls-files` reports no tracked .beads/issues.jsonl.

        Technique: Error Guessing — anticipates a `git add -A` re-tracking the
        export despite the ignore rules.
        """
        # Arrange
        if shutil.which("git") is None:
            pytest.skip("git is required to inspect the index")

        # Act
        result = subprocess.run(
            ["git", "ls-files", "--", _EXPORT_PATH],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
        assert result.stdout == "", (
            f"{_EXPORT_PATH} is tracked by git — it embeds personal data"
            " (F-SC1 / CWE-359). Run: git rm --cached .beads/issues.jsonl"
        )

    @pytest.mark.parametrize("path", _DB_PATHS)
    def test_database_is_not_tracked_by_git(self, path: str) -> None:
        """`git ls-files` reports nothing tracked under the DB directories.

        Technique: Error Guessing — anticipates a `git add -A` run from a clone
        whose `.beads/.gitignore` was removed, staging the whole database.
        """
        # Arrange
        if shutil.which("git") is None:
            pytest.skip("git is required to inspect the index")

        # Act
        result = subprocess.run(
            ["git", "ls-files", "--", path],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
        assert result.stdout == "", (
            f"{path} is tracked by git — the Dolt database is replicated over the"
            f" Dolt remote, never committed. Run: git rm -r --cached {path}"
        )
