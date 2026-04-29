"""Unit tests for docs/postprocess.py — ADR auto-linking post-processor.

Test Techniques Used:
- Boundary Value Analysis: depth calculation for root (depth=0) vs. nested pages
- Equivalence Partitioning: text inside skip-zones vs. plain text
- Specification-based Testing: verifying all four skip-zone types are honoured
- Error Guessing: unknown ADR numbers, invalid slug filenames, empty site dir
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# postprocess.py lives in docs/ (not a package), so add docs/ to sys.path.
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "docs"))

import postprocess  # noqa: E402

# =============================================================================
# _adr_prefix
# =============================================================================


class TestAdrPrefix:
    """Verify relative-path prefix calculation for different page depths.

    Technique: Boundary Value Analysis — root page (depth=0) is the
    off-by-one edge case; deeper pages exercise the general formula.
    """

    def test_root_page_returns_no_dotdot(self, tmp_path: Path) -> None:
        """site/index.html is depth 0 → prefix must be 'adr/', not '../adr/'.

        Technique: Boundary Value Analysis — this is the bug the fix targets.
        """
        # Arrange
        html = tmp_path / "index.html"

        # Act
        result = postprocess._adr_prefix(html, tmp_path)

        # Assert
        assert result == "adr/"

    def test_one_level_deep(self, tmp_path: Path) -> None:
        """site/guides/index.html → depth 1 → '../adr/'."""
        html = tmp_path / "guides" / "index.html"

        result = postprocess._adr_prefix(html, tmp_path)

        assert result == "../adr/"

    def test_two_levels_deep(self, tmp_path: Path) -> None:
        """site/reference/api/index.html → depth 2 → '../../adr/'."""
        html = tmp_path / "reference" / "api" / "index.html"

        result = postprocess._adr_prefix(html, tmp_path)

        assert result == "../../adr/"


# =============================================================================
# _link_adrs
# =============================================================================


class TestLinkAdrs:
    """Verify ADR text substitution and skip-zone preservation.

    Technique: Equivalence Partitioning — plain text (rewrite) vs. protected
    contexts (skip-zones). Specification-based for the four skip-zone types.
    """

    SLUGS = {"001": "ADR-001-example", "038": "ADR-038-deferred-enabled"}

    def test_plain_text_is_linked(self) -> None:
        """Bare ADR-NNN in plain HTML text is replaced with a hyperlink."""
        html = "<p>See ADR-001 for details.</p>"

        result = postprocess._link_adrs(html, "adr/", self.SLUGS)

        assert '<a href="adr/ADR-001-example/">ADR-001</a>' in result

    def test_unknown_adr_left_unchanged(self) -> None:
        """ADR number not in slug map is emitted verbatim."""
        html = "<p>See ADR-999 for details.</p>"

        result = postprocess._link_adrs(html, "adr/", self.SLUGS)

        assert "ADR-999" in result
        assert "<a " not in result

    def test_inside_anchor_skipped(self) -> None:
        """ADR text inside an existing <a> tag must not be double-wrapped.

        Technique: Specification-based — skip-zone contract for <a>.
        """
        html = '<a href="/adr/ADR-001-example/">ADR-001</a>'

        result = postprocess._link_adrs(html, "adr/", self.SLUGS)

        # Must remain a single <a>, not nested anchors.
        assert result.count("<a ") == 1
        assert result == html

    def test_inside_code_tag_skipped(self) -> None:
        """ADR text inside <code> must not be linked."""
        html = "<p>Use the <code>ADR-001</code> pattern.</p>"

        result = postprocess._link_adrs(html, "adr/", self.SLUGS)

        assert "<code>ADR-001</code>" in result
        assert result.count("<a ") == 0

    def test_inside_pre_tag_skipped(self) -> None:
        """ADR text inside <pre> must not be linked."""
        html = "<pre>ADR-001\nADR-038</pre>"

        result = postprocess._link_adrs(html, "adr/", self.SLUGS)

        assert result == html

    def test_inside_title_tag_skipped(self) -> None:
        """ADR text inside <title> must not be linked (page metadata protection)."""
        html = "<title>ADR-001 — Cosalette</title><p>See ADR-001.</p>"

        result = postprocess._link_adrs(html, "adr/", self.SLUGS)

        # Title preserved verbatim; body text linked.
        assert "<title>ADR-001 — Cosalette</title>" in result
        assert '<a href="adr/ADR-001-example/">ADR-001</a>' in result

    def test_inside_head_section_skipped(self) -> None:
        """ADR text inside <head> must not be linked (meta, og:, canonical)."""
        html = "<head><meta content='ADR-001 docs'/></head><body><p>ADR-001</p></body>"

        result = postprocess._link_adrs(html, "adr/", self.SLUGS)

        assert "<head><meta content='ADR-001 docs'/></head>" in result
        assert '<a href="adr/ADR-001-example/">ADR-001</a>' in result

    def test_prefix_applied_to_href(self) -> None:
        """The supplied prefix appears verbatim in the generated href."""
        html = "<p>ADR-038</p>"

        result = postprocess._link_adrs(html, "../../adr/", self.SLUGS)

        assert 'href="../../adr/ADR-038-deferred-enabled/"' in result


# =============================================================================
# _build_slug_map
# =============================================================================


class TestBuildSlugMap:
    """Verify ADR filename discovery and slug validation.

    Technique: Error Guessing — filenames with invalid characters must be
    excluded to prevent href injection.
    """

    def test_valid_adr_files_included(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Canonical ADR-NNN-slug files are mapped by zero-padded number."""
        (tmp_path / "ADR-001-framework-architecture.md").touch()
        (tmp_path / "ADR-038-deferred-enabled.md").touch()
        monkeypatch.setattr(postprocess, "ADR_DIR", tmp_path)

        result = postprocess._build_slug_map()

        assert result["001"] == "ADR-001-framework-architecture"
        assert result["038"] == "ADR-038-deferred-enabled"

    def test_invalid_slug_filenames_excluded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Filenames with uppercase slug body or special chars are skipped.

        Technique: Error Guessing — protects href construction from injection.
        """
        (tmp_path / "ADR-001-Valid-slug.md").touch()  # uppercase V — invalid
        (tmp_path / 'ADR-002-bad"quotes.md').touch()  # quote char — invalid
        (tmp_path / "ADR-003-good-slug.md").touch()  # valid
        monkeypatch.setattr(postprocess, "ADR_DIR", tmp_path)

        result = postprocess._build_slug_map()

        assert "001" not in result
        assert "002" not in result
        assert result["003"] == "ADR-003-good-slug"

    def test_empty_dir_returns_empty_map(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ADR files → empty dict (not an error)."""
        monkeypatch.setattr(postprocess, "ADR_DIR", tmp_path)

        result = postprocess._build_slug_map()

        assert result == {}


# =============================================================================
# process (integration)
# =============================================================================


class TestProcess:
    """End-to-end tests for the process() entry point.

    Technique: Specification-based — verifies the public contract: files
    changed are returned as count, unchanged files are left alone, root-level
    pages get correct (non-broken) links.
    """

    def _make_site(self, tmp_path: Path, adr_dir: Path) -> tuple[Path, Path]:
        """Helper: create a minimal site/ structure for integration tests."""
        site = tmp_path / "site"
        site.mkdir()
        root_page = site / "index.html"
        root_page.write_text("<p>ADR-001</p>", encoding="utf-8")
        nested = site / "reference" / "api"
        nested.mkdir(parents=True)
        nested_page = nested / "index.html"
        nested_page.write_text("<p>ADR-001</p>", encoding="utf-8")
        (adr_dir / "ADR-001-example.md").touch()
        return root_page, nested_page

    def test_returns_count_of_changed_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        monkeypatch.setattr(postprocess, "ADR_DIR", adr_dir)
        root_page, nested_page = self._make_site(tmp_path, adr_dir)
        site = tmp_path / "site"

        count = postprocess.process(site)

        assert count == 2

    def test_root_page_link_is_not_broken(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """site/index.html must use 'adr/' prefix, not '../adr/'.

        Technique: Boundary Value Analysis — root page is the depth=0 edge case.
        """
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        monkeypatch.setattr(postprocess, "ADR_DIR", adr_dir)
        root_page, _ = self._make_site(tmp_path, adr_dir)
        site = tmp_path / "site"

        postprocess.process(site)

        content = root_page.read_text(encoding="utf-8")
        assert 'href="adr/ADR-001-example/"' in content
        assert 'href="../adr/' not in content

    def test_nested_page_link_uses_correct_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """site/reference/api/index.html must use '../../adr/' prefix."""
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        monkeypatch.setattr(postprocess, "ADR_DIR", adr_dir)
        _, nested_page = self._make_site(tmp_path, adr_dir)
        site = tmp_path / "site"

        postprocess.process(site)

        content = nested_page.read_text(encoding="utf-8")
        assert 'href="../../adr/ADR-001-example/"' in content

    def test_unchanged_files_not_rewritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Files with no ADR references are not touched.

        Technique: Equivalence Partitioning — no-match input class.
        """
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        (adr_dir / "ADR-001-example.md").touch()
        monkeypatch.setattr(postprocess, "ADR_DIR", adr_dir)
        site = tmp_path / "site"
        site.mkdir()
        page = site / "index.html"
        page.write_text("<p>No references here.</p>", encoding="utf-8")
        mtime_before = page.stat().st_mtime

        postprocess.process(site)

        assert page.stat().st_mtime == mtime_before

    def test_empty_slug_map_returns_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No ADR files → 0 changes + warning on stderr."""
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        monkeypatch.setattr(postprocess, "ADR_DIR", adr_dir)
        site = tmp_path / "site"
        site.mkdir()

        count = postprocess.process(site)

        assert count == 0
        assert "WARNING" in capsys.readouterr().err
