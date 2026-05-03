from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_render_adr_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "render_adr.py"
    spec = importlib.util.spec_from_file_location("render_adr", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_new_payload() -> dict:
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


def test_validate_new_rejects_malformed_option() -> None:
    module = _load_render_adr_module()
    payload = _valid_new_payload()
    payload["considered_options"][0].pop("description")
    try:
        module.validate(payload)
    except ValueError as exc:
        assert "considered_options[0].description" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing option description")


def test_validate_new_rejects_invalid_matrix_score_type() -> None:
    module = _load_render_adr_module()
    payload = _valid_new_payload()
    payload["impact"] = "moderate"
    payload["decision_matrix"] = [
        {
            "criterion": "Maintainability",
            "scores": {"Option A": "5", "Option B": 4},
        },
        {
            "criterion": "Complexity",
            "scores": {"Option A": 4, "Option B": 3},
        },
        {
            "criterion": "Adoption",
            "scores": {"Option A": 4, "Option B": 4},
        },
    ]

    try:
        module.validate(payload)
    except ValueError as exc:
        assert "decision_matrix[0].scores.Option A" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid matrix score type")


def test_find_adr_file_raises_on_ambiguous_match(tmp_path: Path) -> None:
    module = _load_render_adr_module()
    (tmp_path / "ADR-006-foo.md").write_text("", encoding="utf-8")
    (tmp_path / "ADR-006-bar.md").write_text("", encoding="utf-8")

    try:
        module.find_adr_file(tmp_path, "ADR-006")
    except ValueError as exc:
        assert "Multiple files found" in str(exc)
    else:
        raise AssertionError("Expected ValueError for ambiguous ADR match")
