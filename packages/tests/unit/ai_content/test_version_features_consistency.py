"""Guard tests for VERSION_FEATURES ↔ CHANGELOG version-key consistency.

`VERSION_FEATURES` in ``cosalette._ai_content._meta`` powers ``cosalette ai``
what's-new / prime output. Its keys must name the version each feature was
*actually released* in. This project's release-please config sets
``bump-patch-for-minor-pre-major``, so ``feat:``/``fix:`` commits bump the PATCH
version and only breaking changes bump the minor. Entries must not be pre-keyed
under never-released minor versions.
These guards make that drift a CI failure instead of silent bad output.

Test Techniques Used:
- Specification-based: keys must correspond to released CHANGELOG versions.
- Boundary Value Analysis: the boundary is the latest released version — keys
  at/below it must be released; at most one may sit above it (the pending release).
- Error Guessing: entries keyed under future, never-released minor versions.
"""

from __future__ import annotations

import re
from pathlib import Path

from packaging.version import Version

from cosalette._ai_content._meta import VERSION_FEATURES

_VERSION_HEADING = re.compile(r"^#+\s*\[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def _repo_root() -> Path:
    """Return the repository root by walking up until CHANGELOG.md is found."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "CHANGELOG.md").is_file():
            return parent
    raise RuntimeError("CHANGELOG.md not found in any parent directory")


def _released_versions() -> set[Version]:
    """Parse the set of released versions from CHANGELOG.md headings."""
    changelog = (_repo_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    return {Version(m) for m in _VERSION_HEADING.findall(changelog)}


def _feature_versions() -> list[Version]:
    """Parse VERSION_FEATURES keys as Version objects (fails loudly on bad keys)."""
    return [Version(key) for key in VERSION_FEATURES]


class TestVersionFeaturesConsistency:
    """VERSION_FEATURES keys must match actual releases."""

    def test_changelog_is_parseable(self) -> None:
        """CHANGELOG.md yields at least one released version.

        Technique: Specification-based — guards the fixture the other tests rely
        on so a parsing regression surfaces as its own clear failure.
        """
        assert _released_versions(), "No version headings parsed from CHANGELOG.md"

    def test_every_feature_key_is_valid_semver(self) -> None:
        """Every VERSION_FEATURES key parses as a valid version string."""
        # Act / Assert — Version() raises InvalidVersion on a malformed key.
        assert _feature_versions()

    def test_released_keys_exist_in_changelog(self) -> None:
        """Keys at/below the latest release must name a real CHANGELOG version.

        Technique: Specification-based — a key that claims a shipped version must
        correspond to an actual release, catching entries misnumbered below the
        current release.
        """
        released = _released_versions()
        latest = max(released)

        offenders = sorted(
            (
                key
                for key in VERSION_FEATURES
                if Version(key) <= latest and Version(key) not in released
            ),
            key=Version,
        )

        assert not offenders, (
            f"VERSION_FEATURES keys {offenders} are at/below the latest release "
            f"({latest}) but are absent from CHANGELOG.md — they name versions "
            f"that were never released. Re-key them to the version each feature "
            f"actually shipped in (cross-check CHANGELOG.md)."
        )

    def test_at_most_one_unreleased_key(self) -> None:
        """At most one key may exceed the latest release (the pending version).

        Technique: Boundary Value Analysis + Error Guessing — reproduces the
        historical drift where entries for features that shipped as 0.5.x patches
        were pre-keyed under 0.6.0/0.6.1/0.6.2. release-please uses
        bump-patch-for-minor-pre-major, so feat:/fix: bump PATCH — never pre-key
        entries under a future minor.
        """
        released = _released_versions()
        latest = max(released)

        pending = sorted(
            (key for key in VERSION_FEATURES if Version(key) > latest),
            key=Version,
        )

        assert len(pending) <= 1, (
            f"Multiple unreleased VERSION_FEATURES keys {pending} exceed the "
            f"latest release ({latest}). Under bump-patch-for-minor-pre-major a "
            f"non-breaking change bumps the PATCH version, so there should be at "
            f"most one pending entry. Re-key these to the actual patch versions."
        )
