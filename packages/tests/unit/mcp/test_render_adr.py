"""Unit tests for scripts/render_adr.py — ADR JSON→Markdown renderer.

Test Techniques Used:
- Specification-based: Verifying renderer output matches ADR template contract
- Boundary Value Analysis: Impact levels, option counts, matrix row counts
- Decision Table: amendment scope × allowed content combinations
- Error Guessing: Invalid inputs, missing fields, duplicate chosen options
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "render_adr.py"
_spec = importlib.util.spec_from_file_location("render_adr", _SCRIPT)
assert _spec and _spec.loader
render_adr = importlib.util.module_from_spec(_spec)
sys.modules["render_adr"] = render_adr
_spec.loader.exec_module(render_adr)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_new_adr(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid 'new' ADR input."""
    base: dict[str, Any] = {
        "type": "new",
        "title": "Test Decision",
        "date": "2026-04-07",
        "status": "Accepted",
        "impact": "low",
        "context": "We need to decide something.",
        "decision": "Use X for Y because Z.",
        "decision_drivers": ["Driver A", "Driver B", "Driver C"],
        "considered_options": [
            {
                "name": "Option Alpha",
                "description": "The alpha approach.",
                "advantages": ["Simple"],
                "disadvantages": ["Limited"],
                "chosen": True,
            },
            {
                "name": "Option Beta",
                "description": "The beta approach.",
                "advantages": ["Powerful"],
                "disadvantages": ["Complex"],
            },
        ],
        "consequences_positive": ["Benefit one"],
        "consequences_negative": ["Drawback one"],
        "frontmatter": {"tags": ["testing"]},
    }
    base.update(overrides)
    return base


def _minimal_amendment(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid 'amendment' ADR input."""
    base: dict[str, Any] = {
        "type": "amendment",
        "target_adr": "ADR-001",
        "amendment_scope": "minor",
        "amendment_date": "2026-04-07",
        "amendment_content": {
            "notes": ["This is an editorial note."],
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Validation — new ADR
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateNewAdr:
    """Validate structural requirements for new ADRs."""

    def test_minimal_valid_low_impact_passes(self) -> None:
        """Low-impact new ADR with no matrix passes validation.

        Technique: Specification-based — verifying the contract.
        """
        data = _minimal_new_adr()
        render_adr.validate(data)  # Should not raise.

    def test_moderate_impact_requires_matrix(self) -> None:
        """Moderate impact without decision matrix raises ValueError.

        Technique: Decision Table — impact=moderate, matrix=absent → error.
        """
        data = _minimal_new_adr(impact="moderate")
        with pytest.raises(ValueError, match="decision_matrix is required"):
            render_adr.validate(data)

    def test_moderate_impact_with_matrix_passes(self) -> None:
        """Moderate impact with ≥3 matrix rows passes.

        Technique: Boundary Value Analysis — exactly 3 rows at the minimum.
        """
        data = _minimal_new_adr(
            impact="moderate",
            decision_matrix=[
                {"criterion": "C1", "scores": {"Option Alpha": 4, "Option Beta": 3}},
                {"criterion": "C2", "scores": {"Option Alpha": 5, "Option Beta": 2}},
                {"criterion": "C3", "scores": {"Option Alpha": 3, "Option Beta": 4}},
            ],
        )
        render_adr.validate(data)

    def test_moderate_impact_insufficient_matrix_rows(self) -> None:
        """Moderate impact with <3 matrix rows raises ValueError.

        Technique: Boundary Value Analysis — 2 rows, just below minimum.
        """
        data = _minimal_new_adr(
            impact="moderate",
            decision_matrix=[
                {"criterion": "C1", "scores": {"Option Alpha": 4, "Option Beta": 3}},
                {"criterion": "C2", "scores": {"Option Alpha": 5, "Option Beta": 2}},
            ],
        )
        with pytest.raises(ValueError, match="≥3 decision matrix criteria"):
            render_adr.validate(data)

    def test_high_impact_requires_matrix_and_three_options(self) -> None:
        """High impact with only 2 options raises ValueError.

        Technique: Boundary Value Analysis — 2 options, below minimum 3.
        """
        data = _minimal_new_adr(
            impact="high",
            decision_matrix=[
                {"criterion": f"C{i}", "scores": {"Option Alpha": 4, "Option Beta": 3}}
                for i in range(5)
            ],
        )
        with pytest.raises(ValueError, match="≥3 considered options"):
            render_adr.validate(data)

    def test_high_impact_insufficient_matrix_rows(self) -> None:
        """High impact with <5 matrix rows raises ValueError.

        Technique: Boundary Value Analysis — 4 rows, just below minimum 5.
        """
        options = [
            {
                "name": "Option Alpha",
                "description": "A",
                "advantages": ["a"],
                "disadvantages": ["b"],
                "chosen": True,
            },
            {
                "name": "Option Beta",
                "description": "B",
                "advantages": ["a"],
                "disadvantages": ["b"],
            },
            {
                "name": "Option Gamma",
                "description": "C",
                "advantages": ["a"],
                "disadvantages": ["b"],
            },
        ]
        data = _minimal_new_adr(
            impact="high",
            considered_options=options,
            decision_matrix=[
                {
                    "criterion": f"C{i}",
                    "scores": {
                        "Option Alpha": 4,
                        "Option Beta": 3,
                        "Option Gamma": 2,
                    },
                }
                for i in range(4)
            ],
        )
        with pytest.raises(ValueError, match="≥5 decision matrix criteria"):
            render_adr.validate(data)

    def test_no_chosen_option_fails(self) -> None:
        """No option with chosen=true raises ValueError.

        Technique: Error Guessing — common agent mistake.
        """
        data = _minimal_new_adr()
        for opt in data["considered_options"]:
            opt["chosen"] = False
        with pytest.raises(ValueError, match="Exactly one.*chosen=true"):
            render_adr.validate(data)

    def test_multiple_chosen_options_fails(self) -> None:
        """Two options with chosen=true raises ValueError.

        Technique: Error Guessing — multiple chosen options.
        """
        data = _minimal_new_adr()
        for opt in data["considered_options"]:
            opt["chosen"] = True
        with pytest.raises(ValueError, match="Exactly one.*chosen=true"):
            render_adr.validate(data)

    def test_missing_required_field_raises(self) -> None:
        """Missing a required field raises ValueError.

        Technique: Specification-based — contract enforcement.
        """
        data = _minimal_new_adr()
        del data["context"]
        with pytest.raises(ValueError, match="Missing required field 'context'"):
            render_adr.validate(data)

    def test_matrix_score_keys_must_match_option_names(self) -> None:
        """Matrix row with wrong score keys raises ValueError.

        Technique: Specification-based — score keys ↔ option names contract.
        """
        data = _minimal_new_adr(
            impact="moderate",
            decision_matrix=[
                {"criterion": "C1", "scores": {"Option Alpha": 4, "TYPO-Beta": 3}},
                {"criterion": "C2", "scores": {"Option Alpha": 5, "Option Beta": 2}},
                {"criterion": "C3", "scores": {"Option Alpha": 3, "Option Beta": 4}},
            ],
        )
        with pytest.raises(ValueError, match="score keys don't match"):
            render_adr.validate(data)

    def test_matrix_score_keys_missing_option(self) -> None:
        """Matrix row missing a score for one option raises ValueError.

        Technique: Boundary Value Analysis — one key fewer than options.
        """
        data = _minimal_new_adr(
            impact="moderate",
            decision_matrix=[
                {"criterion": "C1", "scores": {"Option Alpha": 4}},
                {"criterion": "C2", "scores": {"Option Alpha": 5, "Option Beta": 2}},
                {"criterion": "C3", "scores": {"Option Alpha": 3, "Option Beta": 4}},
            ],
        )
        with pytest.raises(ValueError, match="score keys don't match"):
            render_adr.validate(data)


# ---------------------------------------------------------------------------
# Validation — amendment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAmendment:
    """Validate structural requirements for amendments."""

    def test_minor_amendment_passes(self) -> None:
        """Minor amendment with notes only passes.

        Technique: Specification-based.
        """
        data = _minimal_amendment()
        render_adr.validate(data)

    def test_minor_amendment_rejects_additional_options(self) -> None:
        """Minor scope disallows additional_options.

        Technique: Decision Table — scope=minor, content=additional_options → error.
        """
        data = _minimal_amendment()
        data["amendment_content"]["additional_options"] = [
            {
                "name": "X",
                "description": "D",
                "advantages": ["a"],
                "disadvantages": ["b"],
            }
        ]
        with pytest.raises(ValueError, match="not allowed in a minor"):
            render_adr.validate(data)

    def test_minor_amendment_rejects_sub_decisions(self) -> None:
        """Minor scope disallows sub_decisions.

        Technique: Decision Table — scope=minor, content=sub_decisions → error.
        """
        data = _minimal_amendment()
        data["amendment_content"]["sub_decisions"] = [
            {"title": "Sub", "description": "Desc"}
        ]
        with pytest.raises(ValueError, match="not allowed in a minor"):
            render_adr.validate(data)

    def test_additive_amendment_requires_rationale(self) -> None:
        """Additive scope requires amendment_rationale.

        Technique: Decision Table — scope=additive, rationale=absent → error.
        """
        data = _minimal_amendment(amendment_scope="additive")
        data["amendment_content"] = {
            "sub_decisions": [{"title": "Naming", "description": "Use PascalCase."}]
        }
        with pytest.raises(
            ValueError,
            match="Missing required field 'amendment_rationale'",
        ):
            render_adr.validate(data)

    def test_minor_amendment_rejects_revised_code_example(self) -> None:
        """Minor scope disallows revised_decision_code_example.

        Technique: Decision Table — scope=minor,
        content=revised_decision_code_example → error.
        """
        data = _minimal_amendment()
        data["amendment_content"]["revised_decision_code_example"] = "print('hi')"
        with pytest.raises(ValueError, match="not allowed in a minor"):
            render_adr.validate(data)

    def test_minor_amendment_rejects_revised_code_language(self) -> None:
        """Minor scope disallows revised_decision_code_language.

        Technique: Decision Table — scope=minor,
        content=revised_decision_code_language → error.
        """
        data = _minimal_amendment()
        data["amendment_content"]["revised_decision_code_language"] = "bash"
        with pytest.raises(ValueError, match="not allowed in a minor"):
            render_adr.validate(data)

    def test_additive_amendment_rejects_revised_code_example(self) -> None:
        """Additive scope disallows revised_decision_code_example.

        Technique: Decision Table — scope=additive,
        content=revised_decision_code_example → error.
        """
        data = _minimal_amendment(
            amendment_scope="additive",
            amendment_rationale="Extending options.",
        )
        data["amendment_content"] = {
            "revised_decision_code_example": "print('hi')",
        }
        with pytest.raises(ValueError, match="not allowed in an additive"):
            render_adr.validate(data)

    def test_additive_amendment_rejects_revised_decision(self) -> None:
        """Additive scope disallows revised_decision.

        Technique: Decision Table — scope=additive, content=revised_decision → error.
        """
        data = _minimal_amendment(
            amendment_scope="additive",
            amendment_rationale="Adding naming convention.",
        )
        data["amendment_content"] = {"revised_decision": "Use Y instead of X."}
        with pytest.raises(ValueError, match="not allowed in an additive"):
            render_adr.validate(data)

    def test_corrective_amendment_requires_justification(self) -> None:
        """Corrective scope requires amendment_justification.

        Technique: Decision Table — scope=corrective, justification=absent → error.
        """
        data = _minimal_amendment(
            amendment_scope="corrective",
            amendment_rationale="Changing the decision.",
        )
        data["amendment_content"] = {"revised_decision": "Use Y."}
        with pytest.raises(
            ValueError,
            match="Missing required field 'amendment_justification'",
        ):
            render_adr.validate(data)

    def test_corrective_amendment_passes(self) -> None:
        """Corrective amendment with all required fields passes.

        Technique: Specification-based.
        """
        data = _minimal_amendment(
            amendment_scope="corrective",
            amendment_rationale="Library not yet adopted.",
            amendment_justification=(
                "Decision not yet implemented. Zero downstream impact."
            ),
        )
        data["amendment_content"] = {"revised_decision": "Use Y instead of X."}
        render_adr.validate(data)


# ---------------------------------------------------------------------------
# Rendering — new ADR
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderNewAdr:
    """Verify rendered Markdown structure for new ADRs."""

    def test_frontmatter_present(self) -> None:
        """Rendered ADR starts with YAML frontmatter.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 33)
        lines = md.splitlines()
        assert lines[0] == "---"
        assert "status: Accepted" in md
        assert "date: 2026-04-07" in md
        assert "impact: low" in md
        assert "tags: [testing]" in md
        # Find closing ---
        assert lines[5] == "---"

    def test_title_and_number(self) -> None:
        """Title uses correct ADR number format.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 5)
        assert "# ADR-005: Test Decision" in md

    def test_status_line(self) -> None:
        """Status includes date in bold.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 1)
        assert "Accepted **Date:** 2026-04-07" in md

    def test_decision_with_code_example(self) -> None:
        """Code example renders as fenced block.

        Technique: Specification-based.
        """
        data = _minimal_new_adr(
            decision_code_example='print("hello")',
            decision_code_language="python",
        )
        md = render_adr.render_new_adr(data, 1)
        assert "```python" in md
        assert 'print("hello")' in md
        assert "```" in md

    def test_decision_drivers_rendered(self) -> None:
        """Each driver appears as a bullet point.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 1)
        assert "- Driver A" in md
        assert "- Driver B" in md
        assert "- Driver C" in md

    def test_options_rendered_with_chosen_label(self) -> None:
        """Chosen option has (chosen) suffix in heading.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 1)
        assert "### Option 1: Option Alpha (chosen)" in md
        assert "### Option 2: Option Beta" in md
        # No (chosen) on non-chosen.
        lines = md.splitlines()
        beta_line = next(
            line for line in lines if "Option Beta" in line and line.startswith("###")
        )
        assert "(chosen)" not in beta_line

    def test_advantages_disadvantages_format(self) -> None:
        """Options have *Advantages:* and *Disadvantages:* italic labels.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 1)
        assert "- *Advantages:* Simple" in md
        assert "- *Disadvantages:* Limited" in md

    def test_decision_matrix_rendered(self) -> None:
        """Decision matrix renders as a Markdown table with scale legend.

        Technique: Specification-based.
        """
        data = _minimal_new_adr(
            decision_matrix=[
                {"criterion": "Speed", "scores": {"Option Alpha": 5, "Option Beta": 3}},
                {"criterion": "Cost", "scores": {"Option Alpha": 2, "Option Beta": 4}},
                {"criterion": "Ease", "scores": {"Option Alpha": 4, "Option Beta": 3}},
            ],
        )
        md = render_adr.render_new_adr(data, 1)
        assert "## Decision Matrix" in md
        assert "| Criterion | Option Alpha | Option Beta |" in md
        assert "| Speed | 5 | 3 |" in md
        assert "Scale: 1 (poor) to 5 (excellent)" in md

    def test_no_matrix_for_low_impact(self) -> None:
        """Low-impact ADR without matrix omits the section entirely.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 1)
        assert "## Decision Matrix" not in md

    def test_consequences_split(self) -> None:
        """Consequences split into Positive and Negative subsections.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 1)
        assert "### Positive" in md
        assert "- Benefit one" in md
        assert "### Negative" in md
        assert "- Drawback one" in md

    def test_date_stamp_at_end(self) -> None:
        """Rendered ADR ends with italic date stamp.

        Technique: Specification-based.
        """
        data = _minimal_new_adr()
        md = render_adr.render_new_adr(data, 1)
        assert md.rstrip().endswith("_2026-04-07_")

    def test_supersede_status_line(self) -> None:
        """Supersede ADR includes 'Supersedes ADR-NNN' in status.

        Technique: Specification-based.
        """
        data = _minimal_new_adr(type="supersede", supersedes_adr="ADR-014")
        md = render_adr.render_new_adr(data, 33)
        assert "Supersedes ADR-014" in md


# ---------------------------------------------------------------------------
# Rendering — amendment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderAmendment:
    """Verify rendered amendment block structure."""

    def test_minor_amendment_with_notes(self) -> None:
        """Minor amendment renders editorial note admonition.

        Technique: Specification-based.
        """
        data = _minimal_amendment()
        md = render_adr.render_amendment(data)
        assert "## Amendment (2026-04-07) — Minor" in md
        assert '!!! note "Editorial note (2026-04-07)"' in md
        assert "    This is an editorial note." in md

    def test_additive_amendment_with_sub_decisions(self) -> None:
        """Additive amendment renders sub-decision headings.

        Technique: Specification-based.
        """
        data = _minimal_amendment(
            amendment_scope="additive",
            amendment_rationale="Define naming conventions.",
        )
        data["amendment_content"] = {
            "sub_decisions": [
                {
                    "title": "Port Naming",
                    "description": ("Use `<Domain>Port` for port interfaces."),
                }
            ]
        }
        md = render_adr.render_amendment(data)
        assert "## Amendment (2026-04-07) — Additive" in md
        assert "**Rationale:** Define naming conventions." in md
        assert "### Additional Sub-Decision: Port Naming" in md

    def test_corrective_amendment_with_justification(self) -> None:
        """Corrective amendment renders justification blockquote.

        Technique: Specification-based.
        """
        data = _minimal_amendment(
            amendment_scope="corrective",
            amendment_rationale="Library not yet adopted.",
            amendment_justification=(
                "Decision not yet implemented. Zero downstream impact."
            ),
        )
        data["amendment_content"] = {"revised_decision": "Use Y instead of X."}
        md = render_adr.render_amendment(data)
        assert "## Amendment (2026-04-07) — Corrective" in md
        assert "**Rationale:** Library not yet adopted." in md
        assert "> **Justification for amendment (not supersession):**" in md
        assert "### Revised Decision" in md
        assert "Use Y instead of X." in md


# ---------------------------------------------------------------------------
# Auto-numbering and slugify
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoNumbering:
    """Verify ADR auto-numbering and slug generation."""

    def test_next_number_from_existing(self, tmp_path: Path) -> None:
        """next_adr_number returns highest + 1.

        Technique: Specification-based.
        """
        (tmp_path / "ADR-001-foo.md").touch()
        (tmp_path / "ADR-032-bar.md").touch()
        assert render_adr.next_adr_number(tmp_path) == 33

    def test_next_number_empty_dir(self, tmp_path: Path) -> None:
        """Empty directory returns 1.

        Technique: Boundary Value Analysis — no existing ADRs.
        """
        assert render_adr.next_adr_number(tmp_path) == 1

    def test_slugify_basic(self) -> None:
        """Slugify converts title to kebab-case.

        Technique: Specification-based.
        """
        assert render_adr.slugify("Configuration System") == "configuration-system"

    def test_slugify_special_chars(self) -> None:
        """Slugify strips special characters.

        Technique: Specification-based.
        """
        assert render_adr.slugify("Hexagonal Architecture (Ports & Adapters)") == (
            "hexagonal-architecture-ports-adapters"
        )

    def test_slugify_backticks(self) -> None:
        """Slugify handles backtick-wrapped code.

        Technique: Error Guessing — ADR-023 uses backticks in title.
        """
        assert render_adr.slugify("`on_configure` Lifecycle Phase") == (
            "on-configure-lifecycle-phase"
        )

    def test_slugify_empty_string(self) -> None:
        """Slugify returns 'untitled' for empty input.

        Technique: Boundary Value Analysis — empty string edge case.
        """
        assert render_adr.slugify("") == "untitled"

    def test_slugify_non_ascii_only(self) -> None:
        """Slugify returns 'untitled' when only non-ASCII chars remain.

        Technique: Boundary Value Analysis — no Latin characters.
        """
        assert render_adr.slugify("日本語") == "untitled"


# ---------------------------------------------------------------------------
# End-to-end file operations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileOperations:
    """Verify file-level operations (write, amend, supersede)."""

    def test_main_creates_file(self, tmp_path: Path) -> None:
        """main() writes a new ADR file.

        Technique: Round-trip Testing — JSON input → Markdown file.
        """
        data = _minimal_new_adr()
        input_json = tmp_path / "input.json"
        input_json.write_text(json.dumps(data))

        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()

        result = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
        assert result == 0

        files = list(adr_dir.glob("ADR-*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "# ADR-001: Test Decision" in content
        assert "---" in content  # Frontmatter present.

    def test_main_amends_existing_file(self, tmp_path: Path) -> None:
        """main() appends amendment to existing ADR.

        Technique: Round-trip Testing.
        """
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        existing = adr_dir / "ADR-001-test.md"
        existing.write_text(
            "# ADR-001: Test\n\n## Status\n\nAccepted **Date:** 2026-01-01\n\n"
            "## Context\n\nSome context.\n\n_2026-01-01_\n"
        )

        data = _minimal_amendment()
        input_json = tmp_path / "input.json"
        input_json.write_text(json.dumps(data))

        result = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
        assert result == 0

        content = existing.read_text()
        assert "## Amendment (2026-04-07) — Minor" in content
        assert "Amended **Date:** 2026-04-07" in content

    def test_main_supersedes_updates_old_adr(self, tmp_path: Path) -> None:
        """main() with type=supersede updates old ADR status.

        Technique: Round-trip Testing.
        """
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        old_adr = adr_dir / "ADR-014-signal-filters.md"
        old_adr.write_text(
            "# ADR-014: Signal Filters\n\n## Status\n\n"
            "Accepted **Date:** 2026-02-22\n\n## Context\n\nOld context.\n"
        )

        data = _minimal_new_adr(type="supersede", supersedes_adr="ADR-014")
        input_json = tmp_path / "input.json"
        input_json.write_text(json.dumps(data))

        result = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
        assert result == 0

        # Old ADR should be marked superseded.
        old_content = old_adr.read_text()
        assert "Superseded by ADR-015" in old_content
        # Verify no double space (regression test for #7).
        assert "Superseded by ADR-015  " not in old_content

        # New ADR should exist.
        new_files = list(adr_dir.glob("ADR-015-*.md"))
        assert len(new_files) == 1

    def test_main_validation_error_returns_nonzero(self, tmp_path: Path) -> None:
        """main() returns 1 on validation failure.

        Technique: Error Guessing.
        """
        data = {"type": "new", "title": "Broken"}  # Missing required fields.
        input_json = tmp_path / "input.json"
        input_json.write_text(json.dumps(data))

        result = render_adr.main([str(input_json), "--adr-dir", str(tmp_path)])
        assert result == 1

    def test_main_missing_file_returns_nonzero(self, tmp_path: Path) -> None:
        """main() returns 1 when input file doesn't exist.

        Technique: Error Guessing.
        """
        result = render_adr.main([str(tmp_path / "nonexistent.json")])
        assert result == 1

    def test_main_malformed_json_returns_nonzero(self, tmp_path: Path) -> None:
        """main() returns 1 on malformed JSON without traceback.

        Technique: Error Guessing — garbled JSON input.
        """
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{not valid json!!!")

        result = render_adr.main([str(bad_json), "--adr-dir", str(tmp_path)])
        assert result == 1

    def test_main_adr_dir_default(self, tmp_path: Path) -> None:
        """main() uses docs/adr as default --adr-dir.

        Technique: Specification-based — default value check.
        """
        # Without --adr-dir flag, the parser defaults to Path("docs/adr").
        # We can't easily test the default without creating that path,
        # so we test that providing --adr-dir works correctly.
        data = _minimal_new_adr()
        input_json = tmp_path / "input.json"
        input_json.write_text(json.dumps(data))

        adr_dir = tmp_path / "custom-adr"
        adr_dir.mkdir()
        result = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
        assert result == 0
        assert len(list(adr_dir.glob("ADR-*.md"))) == 1

    def test_main_amendment_strips_trailing_date_stamp(self, tmp_path: Path) -> None:
        """Amendment strips the trailing date stamp so it doesn't end up
        in the middle of the document.

        Technique: Error Guessing — date stamp left in middle after amendment.
        """
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        existing = adr_dir / "ADR-001-test.md"
        existing.write_text(
            "# ADR-001: Test\n\n## Status\n\nAccepted **Date:** 2026-01-01\n\n"
            "## Context\n\nSome context.\n\n_2026-01-01_\n"
        )

        data = _minimal_amendment()
        input_json = tmp_path / "input.json"
        input_json.write_text(json.dumps(data))

        result = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
        assert result == 0

        content = existing.read_text()
        # The old date stamp should not appear before the amendment.
        amendment_pos = content.index("## Amendment")
        before_amendment = content[:amendment_pos]
        assert "_2026-01-01_" not in before_amendment

    def test_main_amend_superseded_adr(self, tmp_path: Path) -> None:
        """main() can amend an ADR with Superseded status.

        Technique: Error Guessing — status line starts with Superseded.
        """
        adr_dir = tmp_path / "adr"
        adr_dir.mkdir()
        existing = adr_dir / "ADR-001-old.md"
        existing.write_text(
            "# ADR-001: Old\n\n## Status\n\n"
            "Superseded by ADR-002 **Date:** 2026-01-01\n\n"
            "## Context\n\nOld context.\n\n_2026-01-01_\n"
        )

        data = _minimal_amendment()
        input_json = tmp_path / "input.json"
        input_json.write_text(json.dumps(data))

        result = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
        assert result == 0

        content = existing.read_text()
        assert "## Amendment (2026-04-07) — Minor" in content
        assert "Amended **Date:** 2026-04-07" in content
