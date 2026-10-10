"""Unit tests for cosalette._dependency_hints — missing-dependency messages.

Test Techniques Used:
- Specification-based Testing: message/hint wording per ADR-033 (2026-10-10)
- Decision Table: {pyyaml, jsonschema} x {with, without alternative}
- Error Guessing: extras table drifting from pyproject.toml
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from cosalette._dependency_hints import OPTIONAL_DEPENDENCIES, missing_dependency

pytestmark = pytest.mark.unit

PYPROJECT = Path(__file__).parents[3] / "pyproject.toml"


@pytest.mark.parametrize(
    ("module", "alternative", "expected_hint"),
    [
        (
            "yaml",
            "use --format json",
            "Hint: add pyyaml (or the cosalette[schema] or cosalette[config-yaml] "
            "extra) to your project dependencies, or use --format json.",
        ),
        (
            "yaml",
            None,
            "Hint: add pyyaml (or the cosalette[schema] or cosalette[config-yaml] "
            "extra) to your project dependencies.",
        ),
        (
            "jsonschema",
            None,
            "Hint: add jsonschema (or the cosalette[schema] extra) to your "
            "project dependencies.",
        ),
    ],
    ids=["pyyaml-with-alternative", "pyyaml-no-alternative", "jsonschema"],
)
def test_missing_dependency_names_package_then_extras(
    module: str, alternative: str | None, expected_hint: str
) -> None:
    """The package comes first, then every extra containing it, then the way out.

    Technique: Decision Table — module x alternative.
    """
    # Act
    message, hint = missing_dependency(module, "Feature", alternative)

    # Assert
    package = OPTIONAL_DEPENDENCIES[module][0]
    assert message == f"Feature requires {package}, which is not installed."
    assert hint == expected_hint


@pytest.mark.parametrize("module", sorted(OPTIONAL_DEPENDENCIES))
def test_missing_dependency_is_installer_neutral(module: str) -> None:
    """No message names an installer, and pyyaml never mentions jsonschema.

    Technique: Error Guessing — the old ``pip install`` wording.
    """
    # Act
    text = " ".join(missing_dependency(module, "Feature", "use x"))

    # Assert
    assert "pip" not in text
    assert "uv " not in text
    if module == "yaml":
        assert "jsonschema" not in text


def test_extras_match_pyproject() -> None:
    """Each package lists exactly the extras that contain it in pyproject.toml.

    Technique: Error Guessing — the table drifting from the packaging metadata.
    """
    # Arrange
    extras = tomllib.loads(PYPROJECT.read_text())["project"]["optional-dependencies"]

    # Act / Assert
    for package, listed in OPTIONAL_DEPENDENCIES.values():
        containing = {
            extra
            for extra, requirements in extras.items()
            if any(req.lower().startswith(package) for req in requirements)
        }
        assert set(listed) == containing, package
