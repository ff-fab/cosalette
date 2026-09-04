"""Unit tests for scripts/render_adr.py — ADR rendering and validation.

Test Techniques Used:
- Error Guessing: Missing required option fields and invalid score types
- Boundary Value Analysis: Ambiguous multi-file match in find_adr_file
- Equivalence Partitioning: Valid vs. invalid input structures for validate()
- Parametrized Testing: Impact-driven matrix, chosen-count, score-key, and
  amendment validation paths
- Idempotence: Repeated status transitions and supersession rewrites
- State Transition Testing: Re-superseding an already-superseded ADR
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture(scope="session")
def render_adr(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """Load scripts/render_adr.py as a module (once per test session)."""
    script_path = Path(__file__).resolve().parents[4] / "scripts" / "render_adr.py"
    spec = importlib.util.spec_from_file_location("render_adr", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# needs Any: values are subscripted (e.g. considered_options[0].pop(...))
def _valid_new_payload() -> dict[str, Any]:
    return {
        "type": "new",
        "title": "Test ADR",
        "date": "2026-04-10",
        "status": "Proposed",
        "impact": "low",
        "context": "Context",
        "decision": "Decision",
        "decision_drivers": ["driver-1", "driver-2", "driver-3"],
        "considered_options": [
            {
                "name": "Option A",
                "description": "Desc A",
                "advantages": ["Adv A"],
                "disadvantages": ["Dis A"],
                "chosen": True,
            },
            {
                "name": "Option B",
                "description": "Desc B",
                "advantages": ["Adv B"],
                "disadvantages": ["Dis B"],
                "chosen": False,
            },
        ],
        "consequences_positive": ["Pos"],
        "consequences_negative": ["Neg"],
        "frontmatter": {"tags": ["architecture"]},
    }


def test_validate_new_rejects_malformed_option(render_adr: ModuleType) -> None:
    payload = _valid_new_payload()
    payload["considered_options"][0].pop("description")

    with pytest.raises(ValueError, match=r"considered_options\[0\]\.description"):
        render_adr.validate(payload)


def test_validate_new_rejects_invalid_matrix_score_type(render_adr: ModuleType) -> None:
    payload = _valid_new_payload()
    payload["impact"] = "moderate"
    payload["decision_matrix"] = [
        {"criterion": "Maintainability", "scores": {"Option A": "5", "Option B": 4}},
        {"criterion": "Complexity", "scores": {"Option A": 4, "Option B": 3}},
        {"criterion": "Adoption", "scores": {"Option A": 4, "Option B": 4}},
    ]

    with pytest.raises(ValueError, match=r"decision_matrix\[0\]\.scores\.Option A"):
        render_adr.validate(payload)


def test_find_adr_file_raises_on_ambiguous_match(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "ADR-006-foo.md").write_text("", encoding="utf-8")
    (tmp_path / "ADR-006-bar.md").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Multiple files found"):
        render_adr.find_adr_file(tmp_path, "ADR-006")


# ---------------------------------------------------------------------------
# Helpers shared by tests
# ---------------------------------------------------------------------------


def _three_options() -> list[dict[str, object]]:
    """Return three considered options with Option C as the chosen one."""
    return [
        {
            "name": "Option A",
            "description": "Desc A",
            "advantages": ["Adv A"],
            "disadvantages": ["Dis A"],
            "chosen": False,
        },
        {
            "name": "Option B",
            "description": "Desc B",
            "advantages": ["Adv B"],
            "disadvantages": ["Dis B"],
            "chosen": False,
        },
        {
            "name": "Option C",
            "description": "Desc C",
            "advantages": ["Adv C"],
            "disadvantages": ["Dis C"],
            "chosen": True,
        },
    ]


def _matrix(options: list[dict[str, object]], n_rows: int) -> list[dict[str, object]]:
    """Return a decision matrix with *n_rows* criteria for *options*."""
    criteria = [
        "Maintainability",
        "Complexity",
        "Adoption",
        "Performance",
        "Testability",
        "Security",
    ]
    names = [o["name"] for o in options]
    return [
        {"criterion": criteria[i], "scores": dict.fromkeys(names, 4)}
        for i in range(n_rows)
    ]


# ---------------------------------------------------------------------------
# Impact-driven matrix enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impact", ["moderate", "high"])
def test_validate_requires_matrix_for_moderate_or_high_impact(
    render_adr: ModuleType, impact: str
) -> None:
    """Moderate/high-impact ADRs without a decision_matrix must be rejected.

    Technique: Equivalence Partitioning — impact levels that require a matrix.
    """
    payload = _valid_new_payload()
    payload["impact"] = impact
    if impact == "high":
        payload["considered_options"] = _three_options()

    with pytest.raises(ValueError, match="decision_matrix is required"):
        render_adr.validate(payload)


@pytest.mark.parametrize(
    ("impact", "n_rows", "match"),
    [
        ("high", 4, "≥5 decision matrix criteria"),
        ("moderate", 2, "≥3 decision matrix criteria"),
    ],
)
def test_validate_rejects_insufficient_matrix_rows(
    render_adr: ModuleType, impact: str, n_rows: int, match: str
) -> None:
    """ADRs must meet the minimum matrix row count for their impact level.

    Technique: Boundary Value Analysis — one below the required minimum.
    """
    opts = _three_options()
    payload = _valid_new_payload()
    payload["impact"] = impact
    payload["considered_options"] = opts
    payload["decision_matrix"] = _matrix(opts, n_rows)

    with pytest.raises(ValueError, match=match):
        render_adr.validate(payload)


def test_validate_high_impact_requires_three_options(render_adr: ModuleType) -> None:
    """High-impact ADRs with fewer than three options must be rejected.

    Technique: Boundary Value Analysis — two options instead of three.
    """
    opts = _matrix(_valid_new_payload()["considered_options"], 5)
    payload = _valid_new_payload()
    payload["impact"] = "high"
    payload["decision_matrix"] = opts
    # default payload has 2 options — below the 3 required for high impact

    with pytest.raises(ValueError, match="≥3 considered options"):
        render_adr.validate(payload)


# ---------------------------------------------------------------------------
# Exactly-one-chosen enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chosen_flags", "match"),
    [
        ([False, False], "Exactly one"),
        ([True, True], "Exactly one"),
    ],
)
def test_validate_rejects_wrong_chosen_count(
    render_adr: ModuleType, chosen_flags: list[bool], match: str
) -> None:
    """validate() rejects ADRs where the chosen option count is not exactly 1.

    Technique: Equivalence Partitioning — zero chosen vs. two chosen.
    """
    payload = _valid_new_payload()
    for opt, flag in zip(payload["considered_options"], chosen_flags, strict=False):
        opt["chosen"] = flag

    with pytest.raises(ValueError, match=match):
        render_adr.validate(payload)


# ---------------------------------------------------------------------------
# Matrix score key matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scores", "match"),
    [
        # Extra key not matching any option name
        ({"Option A": 4, "Option B": 3, "Option X": 5}, "score keys don't match"),
        # Missing one of the option names
        ({"Option A": 4}, "score keys don't match"),
    ],
)
def test_validate_rejects_mismatched_matrix_score_keys(
    render_adr: ModuleType, scores: dict[str, int], match: str
) -> None:
    """validate() rejects matrices whose score keys don't match option names.

    Technique: Error Guessing — extra and missing key variants.
    """
    payload = _valid_new_payload()
    payload["impact"] = "moderate"
    payload["decision_matrix"] = [
        {"criterion": "Maintainability", "scores": scores},
        {"criterion": "Complexity", "scores": {"Option A": 4, "Option B": 3}},
        {"criterion": "Adoption", "scores": {"Option A": 5, "Option B": 4}},
    ]

    with pytest.raises(ValueError, match=match):
        render_adr.validate(payload)


# ---------------------------------------------------------------------------
# Amendment validation paths
# ---------------------------------------------------------------------------


def _valid_amendment(scope: str = "minor") -> dict[str, object]:
    return {
        "type": "amendment",
        "target_adr": "ADR-001-foo.md",
        "amendment_scope": scope,
        "amendment_date": "2026-05-10",
        "amendment_content": {},
    }


def test_validate_amendment_rejects_missing_target_adr(render_adr: ModuleType) -> None:
    """Amendment payloads without target_adr must be rejected.

    Technique: Error Guessing — required top-level field missing.
    """
    payload = _valid_amendment()
    del payload["target_adr"]

    with pytest.raises(ValueError, match="target_adr"):
        render_adr.validate(payload)


def test_validate_additive_amendment_requires_rationale(render_adr: ModuleType) -> None:
    """Additive amendments without amendment_rationale must be rejected.

    Technique: Specification-based — scope-driven field requirement.
    """
    payload = _valid_amendment(scope="additive")

    with pytest.raises(ValueError, match="amendment_rationale"):
        render_adr.validate(payload)


def test_validate_corrective_amendment_requires_justification(
    render_adr: ModuleType,
) -> None:
    """Corrective amendments without amendment_justification must be rejected.

    Technique: Specification-based — stronger scope-driven field requirement.
    """
    payload = _valid_amendment(scope="corrective")
    payload["amendment_rationale"] = "needed"

    with pytest.raises(ValueError, match="amendment_justification"):
        render_adr.validate(payload)


@pytest.mark.parametrize(
    "forbidden",
    [
        "additional_options",
        "additional_matrix_rows",
        "revised_decision",
        "sub_decisions",
    ],
)
def test_validate_minor_amendment_rejects_forbidden_fields(
    render_adr: ModuleType, forbidden: str
) -> None:
    """Minor amendments must not include structural change fields.

    Technique: Equivalence Partitioning — each forbidden field variant.
    """
    payload = _valid_amendment(scope="minor")
    payload["amendment_content"] = {forbidden: "something"}

    with pytest.raises(ValueError, match=f"'{forbidden}' is not allowed"):
        render_adr.validate(payload)


@pytest.mark.parametrize(
    "forbidden",
    [
        "revised_decision",
        "revised_decision_code_example",
        "revised_decision_code_language",
    ],
)
def test_validate_additive_amendment_rejects_decision_revision(
    render_adr: ModuleType, forbidden: str
) -> None:
    """Additive amendments must not revise the decision text.

    Technique: Equivalence Partitioning — each forbidden revision field.
    """
    payload = _valid_amendment(scope="additive")
    payload["amendment_rationale"] = "extending only"
    payload["amendment_content"] = {forbidden: "something"}

    with pytest.raises(ValueError, match="not allowed in an additive amendment"):
        render_adr.validate(payload)


# ---------------------------------------------------------------------------
# Happy-path / BVA: minimum valid boundaries for moderate and high impact
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("impact", "n_rows", "n_options"),
    [
        ("moderate", 3, 2),  # BVA: exactly the minimum 3 rows, 2 options
        ("high", 5, 3),  # BVA: exactly the minimum 5 rows, 3 options
    ],
)
def test_validate_accepts_adr_at_minimum_matrix_boundary(
    render_adr: ModuleType, impact: str, n_rows: int, n_options: int
) -> None:
    """validate() must not raise for ADRs that meet the exact minimum requirements.

    Technique: Boundary Value Analysis — minimum valid matrix size for each
    impact level.  Verifies the boundary is inclusive (>= not >).
    """
    all_options = _three_options()
    opts = all_options[:n_options]
    # Ensure exactly one chosen
    for i, o in enumerate(opts):
        o["chosen"] = i == 0

    payload = _valid_new_payload()
    payload["impact"] = impact
    payload["considered_options"] = opts
    payload["decision_matrix"] = _matrix(opts, n_rows)

    render_adr.validate(payload)  # must not raise


# ---------------------------------------------------------------------------
# Status-transition operation (cos-hoap)
# ---------------------------------------------------------------------------


def _write_adr(
    adr_dir: Path,
    *,
    frontmatter_status: str = "Proposed",
    body_status: str | None = None,
    filename: str = "ADR-099-example.md",
) -> Path:
    """Write a minimal ADR file and return its path.

    *body_status* defaults to *frontmatter_status* so the file starts in sync;
    pass a different value to simulate frontmatter/body drift.
    """
    body = frontmatter_status if body_status is None else body_status
    path = adr_dir / filename
    path.write_text(
        "\n".join(
            [
                "---",
                f"status: {frontmatter_status}",
                "date: 2026-09-01",
                "impact: low",
                "tags: [testing]",
                "---",
                "",
                "# ADR-099: Example",
                "",
                "## Status",
                "",
                body,
                "",
                "## Context",
                "",
                "Body.",
                "",
                "_2026-09-01_",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _valid_status_payload() -> dict[str, object]:
    return {"type": "status", "target_adr": "ADR-099", "status": "Accepted"}


@pytest.mark.parametrize("field", ["target_adr", "status"])
def test_validate_status_rejects_missing_field(
    render_adr: ModuleType, field: str
) -> None:
    """Status payloads without a required field must be rejected.

    Technique: Error Guessing — each required top-level field missing.
    """
    payload = _valid_status_payload()
    del payload[field]

    with pytest.raises(ValueError, match=field):
        render_adr.validate(payload)


@pytest.mark.parametrize("bad_status", ["Superseded by ADR-001", "Deprecated", "done"])
def test_validate_status_rejects_out_of_vocabulary_target(
    render_adr: ModuleType, bad_status: str
) -> None:
    """Only Proposed/Accepted are valid transition targets.

    Technique: Equivalence Partitioning — superseded, unknown, and lowercase
    variants all fall outside the closed vocabulary.
    """
    payload = _valid_status_payload()
    payload["status"] = bad_status

    with pytest.raises(ValueError, match="status must be"):
        render_adr.validate(payload)


def test_validate_status_accepts_valid_payload(render_adr: ModuleType) -> None:
    """A well-formed status payload passes validation.

    Technique: Equivalence Partitioning — the valid partition.
    """
    render_adr.validate(_valid_status_payload())  # must not raise


def test_transition_status_flips_proposed_to_accepted(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Both the frontmatter and the ## Status body line move to Accepted.

    Technique: State Transition Testing — Proposed → Accepted.
    """
    path = _write_adr(tmp_path, body_status="Proposed **Date:** 2026-09-01")

    changed = render_adr.transition_status(path, "Accepted")
    text = path.read_text(encoding="utf-8")

    assert changed is True
    assert "status: Accepted" in text
    # The date tail is preserved; only the leading token changes.
    assert "Accepted **Date:** 2026-09-01" in text
    assert "Proposed" not in text


def test_transition_status_preserves_marker_tail(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Amendment/supersede markers on the Status line survive a transition.

    Technique: Boundary Value Analysis — a Status line carrying extra markers.
    """
    body = "Proposed **Date:** 2026-08-30 | Amended **Date:** 2026-09-01"
    path = _write_adr(tmp_path, body_status=body)

    render_adr.transition_status(path, "Accepted")
    text = path.read_text(encoding="utf-8")

    assert "Accepted **Date:** 2026-08-30 | Amended **Date:** 2026-09-01" in text


def test_transition_status_is_idempotent(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Transitioning to the current status is a no-op that reports no change.

    Technique: Idempotence — applying the same transition twice.
    """
    path = _write_adr(tmp_path, frontmatter_status="Accepted")
    before = path.read_text(encoding="utf-8")

    changed = render_adr.transition_status(path, "Accepted")

    assert changed is False
    assert path.read_text(encoding="utf-8") == before


def test_transition_status_heals_frontmatter_body_drift(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """A transition rewrites both locations even if they already disagree.

    Technique: Error Guessing — pre-existing drift (frontmatter Proposed,
    body already Accepted) must converge on the target status.
    """
    path = _write_adr(
        tmp_path,
        frontmatter_status="Proposed",
        body_status="Accepted **Date:** 2026-09-01",
    )

    changed = render_adr.transition_status(path, "Accepted")
    text = path.read_text(encoding="utf-8")

    assert changed is True
    assert "status: Accepted" in text
    assert "status: Proposed" not in text


def test_transition_status_heals_drift_when_frontmatter_matches_target(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Drift is healed even when the frontmatter already equals the target.

    Technique: Error Guessing — frontmatter reads Accepted (the target) while
    the body line still reads Proposed; the early no-op must not skip healing.
    """
    path = _write_adr(
        tmp_path,
        frontmatter_status="Accepted",
        body_status="Proposed **Date:** 2026-09-01",
    )

    changed = render_adr.transition_status(path, "Accepted")
    text = path.read_text(encoding="utf-8")

    assert changed is True
    assert "status: Accepted" in text
    # The drifted body line is rewritten to the target, tail preserved.
    assert "Accepted **Date:** 2026-09-01" in text
    assert "Proposed" not in text


def test_transition_status_refuses_superseded_adr(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """A superseded ADR cannot be transitioned via the status operation.

    Technique: Error Guessing — the closed vocabulary excludes the current
    superseded status.
    """
    path = _write_adr(
        tmp_path,
        frontmatter_status="Superseded by ADR-100",
        body_status="Superseded by ADR-100 **Date:** 2026-09-01",
    )

    with pytest.raises(ValueError, match="use the supersede operation"):
        render_adr.transition_status(path, "Accepted")


def test_handle_status_raises_on_unknown_adr(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Targeting a non-existent ADR is refused.

    Technique: Error Guessing — the target file does not exist.
    """
    with pytest.raises(FileNotFoundError, match="Cannot find file"):
        render_adr._handle_status(
            {"type": "status", "target_adr": "ADR-404", "status": "Accepted"},
            tmp_path,
        )


def test_main_status_transition_end_to_end(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """The CLI dispatches a status transition and rewrites the target ADR.

    Technique: Integration — exercise validate → dispatch → file rewrite.
    """
    adr_dir = tmp_path / "adr"
    adr_dir.mkdir()
    _write_adr(adr_dir, body_status="Proposed **Date:** 2026-09-01")

    input_json = tmp_path / "input.json"
    input_json.write_text(
        '{"type": "status", "target_adr": "ADR-099", "status": "Accepted"}',
        encoding="utf-8",
    )

    exit_code = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
    text = (adr_dir / "ADR-099-example.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "status: Accepted" in text


# ---------------------------------------------------------------------------
# Supersession pointer paragraph (cos-r9a7)
# ---------------------------------------------------------------------------

_NOTE = (
    "the backend is maturin, not hatchling. The PyPI channel and src layout "
    "recorded here remain valid."
)


def _write_superseded_target(
    adr_dir: Path,
    *,
    frontmatter_status: str = "Accepted",
    body_status: str = "Accepted **Date:** 2026-02-14",
    pointer: str | None = None,
    filename: str = "ADR-014-signal-filters.md",
) -> Path:
    """Write a supersession target ADR and return its path.

    *pointer* injects an existing '**Superseded by:**' paragraph, simulating an
    ADR that has already been through the supersede path once.
    """
    status_block = [body_status] if pointer is None else [body_status, "", pointer]
    path = adr_dir / filename
    path.write_text(
        "\n".join(
            [
                "---",
                f"status: {frontmatter_status}",
                "date: 2026-02-14",
                "impact: moderate",
                "tags: [packaging]",
                "---",
                "",
                "# ADR-014: Signal Filters",
                "",
                "## Status",
                "",
                *status_block,
                "",
                "## Context",
                "",
                "Body.",
                "",
                "_2026-02-14_",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _supersede_payload(**overrides: Any) -> dict[str, Any]:
    """Build a valid 'supersede' payload targeting ADR-014."""
    payload = _valid_new_payload()
    payload.update(
        {"type": "supersede", "supersedes_adr": "ADR-014", "status": "Accepted"}
    )
    payload.update(overrides)
    return payload


def test_update_superseded_status_emits_pointer_paragraph(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """All three supersession markers are written, pointer paragraph included.

    Technique: Specification-based — the established ADR-027/ADR-036/ADR-008
    supersession shape: frontmatter status, ## Status line, pointer paragraph.
    """
    # Arrange
    path = _write_superseded_target(tmp_path)

    # Act
    render_adr.update_superseded_status(
        path, "ADR-070", "ADR-070-maturin-build-backend.md", _NOTE
    )
    text = path.read_text(encoding="utf-8")

    # Assert
    assert "status: Superseded by ADR-070" in text
    assert "Superseded by ADR-070 **Date:** 2026-02-14" in text
    assert (
        "\nSuperseded by ADR-070 **Date:** 2026-02-14\n\n"
        "**Superseded by:** [ADR-070](ADR-070-maturin-build-backend.md) — "
        f"{_NOTE}\n\n## Context\n" in text
    )


def test_update_superseded_status_omitted_note_emits_bare_pointer(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Without a note the pointer is emitted without a dangling separator.

    Technique: Equivalence Partitioning — the note is optional, so the
    absent/blank partition must still yield a structurally complete line.
    """
    # Arrange
    path = _write_superseded_target(tmp_path)

    # Act
    render_adr.update_superseded_status(path, "ADR-070", "ADR-070-maturin.md")
    text = path.read_text(encoding="utf-8")

    # Assert
    assert "**Superseded by:** [ADR-070](ADR-070-maturin.md)\n" in text
    assert "—" not in text


@pytest.mark.parametrize("prefix", ["— ", "–", "- ", ""])
def test_update_superseded_status_normalises_note_separator(
    render_adr: ModuleType, tmp_path: Path, prefix: str
) -> None:
    """The renderer owns the ' — ' separator and never doubles it.

    Technique: Equivalence Partitioning — em dash, en dash, hyphen, and no
    leading dash all normalise to the same canonical pointer line.
    """
    # Arrange
    path = _write_superseded_target(tmp_path)

    # Act
    render_adr.update_superseded_status(
        path, "ADR-070", "ADR-070-maturin.md", f"{prefix}{_NOTE}"
    )
    text = path.read_text(encoding="utf-8")

    # Assert
    assert f"**Superseded by:** [ADR-070](ADR-070-maturin.md) — {_NOTE}\n" in text


def test_update_superseded_status_is_idempotent(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Re-running the same supersession leaves the file byte-identical.

    Technique: Idempotence — applying the same rewrite twice.
    """
    # Arrange
    path = _write_superseded_target(tmp_path)
    render_adr.update_superseded_status(path, "ADR-070", "ADR-070-maturin.md", _NOTE)
    after_first = path.read_text(encoding="utf-8")

    # Act
    render_adr.update_superseded_status(path, "ADR-070", "ADR-070-maturin.md", _NOTE)
    after_second = path.read_text(encoding="utf-8")

    # Assert
    assert after_second == after_first
    assert after_second.count("**Superseded by:**") == 1


def test_update_superseded_status_replaces_stale_pointer(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """Superseding an already-superseded ADR rewrites the pointer in place.

    Technique: State Transition Testing — Superseded by ADR-070 → ADR-071,
    where the stale pointer must be replaced rather than stacked.
    """
    # Arrange
    path = _write_superseded_target(
        tmp_path,
        frontmatter_status="Superseded by ADR-070",
        body_status="Superseded by ADR-070 **Date:** 2026-02-14",
        pointer="**Superseded by:** [ADR-070](ADR-070-maturin.md) — stale prose.",
    )

    # Act
    render_adr.update_superseded_status(
        path, "ADR-071", "ADR-071-successor.md", "fresh prose."
    )
    text = path.read_text(encoding="utf-8")

    # Assert
    assert text.count("**Superseded by:**") == 1
    assert "status: Superseded by ADR-071" in text
    assert "**Superseded by:** [ADR-071](ADR-071-successor.md) — fresh prose.\n" in text
    assert "ADR-070" not in text


def test_update_superseded_status_preserves_amendment_tail(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """The date/amendment tail of the Status line survives supersession.

    Technique: Boundary Value Analysis — a Status line carrying extra markers.
    """
    # Arrange
    body = "Accepted **Date:** 2026-02-14 | Amended **Date:** 2026-05-10"
    path = _write_superseded_target(tmp_path, body_status=body)

    # Act
    render_adr.update_superseded_status(path, "ADR-070", "ADR-070-maturin.md")
    text = path.read_text(encoding="utf-8")

    # Assert
    assert (
        "Superseded by ADR-070 **Date:** 2026-02-14 | Amended **Date:** 2026-05-10"
        in text
    )


def test_validate_supersede_rejects_non_string_note(render_adr: ModuleType) -> None:
    """A non-string supersession_note is refused before any file is written.

    Technique: Error Guessing — a list where prose is expected.
    """
    payload = _supersede_payload(supersession_note=["not", "prose"])

    with pytest.raises(ValueError, match="supersession_note must be a string"):
        render_adr.validate(payload)


def test_main_supersede_links_pointer_to_new_adr_filename(
    render_adr: ModuleType, tmp_path: Path
) -> None:
    """End-to-end: the pointer links to the file the renderer just created.

    Technique: Integration — validate → render → supersede rewrite, asserting
    the link target against the real generated filename.
    """
    # Arrange
    adr_dir = tmp_path / "adr"
    adr_dir.mkdir()
    target = _write_superseded_target(adr_dir)
    input_json = tmp_path / "input.json"
    input_json.write_text(
        json.dumps(_supersede_payload(supersession_note=_NOTE)), encoding="utf-8"
    )

    # Act
    exit_code = render_adr.main([str(input_json), "--adr-dir", str(adr_dir)])
    text = target.read_text(encoding="utf-8")

    # Assert
    assert exit_code == 0
    created = next(p for p in adr_dir.iterdir() if p.name.startswith("ADR-015"))
    assert created.name == "ADR-015-test-adr.md"
    assert f"**Superseded by:** [ADR-015]({created.name}) — {_NOTE}\n" in text
