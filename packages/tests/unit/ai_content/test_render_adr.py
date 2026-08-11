"""Unit tests for scripts/render_adr.py — ADR rendering and validation.

Test Techniques Used:
- Error Guessing: Missing required option fields and invalid score types
- Boundary Value Analysis: Ambiguous multi-file match in find_adr_file
- Equivalence Partitioning: Valid vs. invalid input structures for validate()
- Parametrized Testing: Impact-driven matrix, chosen-count, score-key, and
  amendment validation paths
"""

from __future__ import annotations

import importlib.util
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
