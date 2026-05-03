"""Unit tests for scripts/render_adr.py — ADR rendering and validation.

Test Techniques Used:
- Error Guessing: Missing required option fields and invalid score types
- Boundary Value Analysis: Ambiguous multi-file match in find_adr_file
- Equivalence Partitioning: Valid vs. invalid input structures for validate()
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="session")
def render_adr(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """Load scripts/render_adr.py as a module (once per test session)."""
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
