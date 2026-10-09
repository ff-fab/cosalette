"""Unit tests for import layering — lower layers never import ``cosalette._app``.

``_wiring``, ``_registration`` and ``_runners`` sit below the ``App`` façade. An
import of ``cosalette._app`` from them created the first-access import cycles of
cos-qitr.1, so this static guard keeps the edge from coming back (cos-qitr.3).

Test Techniques Used:
- Error Guessing: a runtime import of ``cosalette._app`` reintroduced in a
  lower-layer module, directly or through a lazy ``cosalette`` re-export.
- Branch Coverage: imports under ``if TYPE_CHECKING:`` are allowed, their
  ``else`` branch and every other position are not.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import cosalette

pytestmark = pytest.mark.unit

SRC = Path(cosalette.__file__).parent
LOWER_LAYERS = ("_wiring", "_registration", "_runners")
FORBIDDEN = "cosalette._app"

#: Lazy re-export name -> source module, e.g. ``App`` -> ``cosalette._app``.
EXPORT_ORIGIN = {
    name: module for module, names in cosalette._EXPORTS.items() for name in names
}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_imports(node: ast.AST) -> Iterator[ast.Import | ast.ImportFrom]:
    """Yield import nodes outside ``if TYPE_CHECKING:`` bodies."""
    for child in ast.iter_child_nodes(node):
        guarded = isinstance(child, ast.If) and _is_type_checking(child.test)
        for stmt in child.orelse if guarded else [child]:
            if isinstance(stmt, ast.Import | ast.ImportFrom):
                yield stmt
            yield from _runtime_imports(stmt)


def _targets(node: ast.Import | ast.ImportFrom, package: str) -> Iterator[str]:
    """Yield the absolute modules an import statement loads."""
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    base = node.module or ""
    if node.level:
        parent = package.rsplit(".", node.level - 1)[0]
        base = f"{parent}.{base}" if base else parent
    yield base
    for alias in node.names:
        target = f"{base}.{alias.name}"
        yield EXPORT_ORIGIN.get(alias.name, target) if base == "cosalette" else target


def _violations(source: str, module: str, package: str) -> list[str]:
    """Return one ``module:line`` entry per statement that loads ``_app``."""
    return [
        f"{module}:{node.lineno} imports {FORBIDDEN}"
        for node in _runtime_imports(ast.parse(source))
        if any(
            target == FORBIDDEN or target.startswith(f"{FORBIDDEN}.")
            for target in _targets(node, package)
        )
    ]


def _lower_layer_files() -> list[Path]:
    return sorted(
        path for layer in LOWER_LAYERS for path in (SRC / layer).rglob("*.py")
    )


def _module_and_package(path: Path) -> tuple[str, str]:
    parts = ("cosalette", *path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
        return ".".join(parts), ".".join(parts)
    return ".".join(parts), ".".join(parts[:-1])


class TestLowerLayersDoNotImportApp:
    """``_wiring``, ``_registration`` and ``_runners`` stay below ``_app``."""

    def test_lower_layer_files_are_found(self) -> None:
        """Technique: Error Guessing — an empty scan would guard nothing."""
        layers = {path.relative_to(SRC).parts[0] for path in _lower_layer_files()}

        assert layers == set(LOWER_LAYERS)

    @pytest.mark.parametrize(
        "path", _lower_layer_files(), ids=lambda p: str(p.relative_to(SRC))
    )
    def test_module_has_no_runtime_app_import(self, path: Path) -> None:
        """Technique: Error Guessing — the cos-qitr.1 cycle edge stays out."""
        # Arrange
        module, package = _module_and_package(path)

        # Act
        violations = _violations(path.read_text(encoding="utf-8"), module, package)

        # Assert
        assert violations == []


class TestViolationDetection:
    """The guard flags each spelling of the forbidden edge and nothing else."""

    @pytest.mark.parametrize(
        "source",
        [
            "import cosalette._app",
            "from cosalette._app import App",
            "from cosalette._app._inbound import x",
            "from cosalette import _app",
            "from cosalette import App",
            "from .._app import App",
            "def f():\n    from cosalette._app import App",
            "if TYPE_CHECKING:\n    pass\nelse:\n    from cosalette._app import App",
        ],
    )
    def test_forbidden_import_is_flagged(self, source: str) -> None:
        """Technique: Error Guessing — every way to spell the edge."""
        # Act
        violations = _violations(source, "cosalette._wiring._x", "cosalette._wiring")

        # Assert
        assert len(violations) == 1

    @pytest.mark.parametrize(
        "source",
        [
            "if TYPE_CHECKING:\n    from cosalette._app import App",
            "if typing.TYPE_CHECKING:\n    from cosalette._app import App",
            "from cosalette._application import x",
            "from cosalette import Router",
            "from cosalette._registration._model import EnabledSpec",
        ],
    )
    def test_allowed_import_is_not_flagged(self, source: str) -> None:
        """Technique: Branch Coverage — TYPE_CHECKING and look-alike modules."""
        # Act
        violations = _violations(source, "cosalette._wiring._x", "cosalette._wiring")

        # Assert
        assert violations == []
