"""Unit tests for import layering — lower layers never import ``cosalette._app``.

``_wiring``, ``_registration``, ``_runners`` and ``_router`` sit below the ``App``
façade. An import of ``cosalette._app`` from the first three created the
first-access import cycles of cos-qitr.1, and ``_router`` must not load the App
it is composed into, so this static guard keeps the edge out (cos-qitr.3).

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
LOWER_LAYERS = ("_wiring", "_registration", "_runners", "_router")
FORBIDDEN = "cosalette._app"

#: Lazy re-export name -> source module, e.g. ``App`` -> ``cosalette._app``.
EXPORT_ORIGIN = {
    name: module for module, names in cosalette._EXPORTS.items() for name in names
}


def _import_bindings(tree: ast.AST, module: str, name: str | None = None) -> set[str]:
    """Collect explicitly imported bindings; arbitrary look-alikes stay runtime."""
    bindings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and name is None:
            bindings.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == module
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module == module
            and not node.level
        ):
            bindings.update(
                alias.asname or alias.name for alias in node.names if alias.name == name
            )
    return bindings


def _runtime_nodes(
    node: ast.AST, typing_modules: set[str], type_checking_names: set[str]
) -> Iterator[ast.AST]:
    """Yield nodes outside bodies guarded by an explicitly imported typing flag."""
    for child in ast.iter_child_nodes(node):
        guarded = isinstance(child, ast.If) and (
            isinstance(child.test, ast.Name)
            and child.test.id in type_checking_names
            or isinstance(child.test, ast.Attribute)
            and isinstance(child.test.value, ast.Name)
            and child.test.value.id in typing_modules
            and child.test.attr == "TYPE_CHECKING"
        )
        for stmt in child.orelse if guarded else [child]:
            yield stmt
            yield from _runtime_nodes(stmt, typing_modules, type_checking_names)


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
        if base == "cosalette" and alias.name == "*":
            yield from EXPORT_ORIGIN.values()
            continue
        target = f"{base}.{alias.name}"
        yield EXPORT_ORIGIN.get(alias.name, target) if base == "cosalette" else target


def _violations(source: str, module: str, package: str) -> list[str]:
    """Return a ``module:line`` entry per import or access that loads ``_app``."""
    tree = ast.parse(source)
    root_names = _import_bindings(tree, "cosalette")
    violations = []
    for node in _runtime_nodes(
        tree,
        _import_bindings(tree, "typing"),
        _import_bindings(tree, "typing", "TYPE_CHECKING"),
    ):
        targets = []
        if isinstance(node, ast.Import | ast.ImportFrom):
            targets = list(_targets(node, package))
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in root_names
        ):
            targets = [EXPORT_ORIGIN.get(node.attr, f"cosalette.{node.attr}")]
        else:
            continue
        if any(
            target == FORBIDDEN or target.startswith(f"{FORBIDDEN}.")
            for target in targets
        ):
            violations.append(f"{module}:{node.lineno} imports {FORBIDDEN}")
    return violations


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
    """Every lower layer stays below ``_app``."""

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
            "from cosalette import *",
            "from .. import *",
            "import cosalette; cosalette.App()",
            "import cosalette as cos; cos.App",
            "import cosalette as cos\ndef f():\n    return cos.App()",
            "import cosalette; cosalette._app.App",
            "import config\nif config.TYPE_CHECKING:\n    from cosalette import App",
            "if TYPE_CHECKING:\n    from cosalette import App",
            "from .._app import App",
            "def f():\n    from cosalette._app import App",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    pass\nelse:\n    from cosalette._app import App",
            "import typing as t\n"
            "if t.TYPE_CHECKING:\n    pass\nelse:\n    from cosalette import *",
            "import cosalette as cos\nfrom typing import TYPE_CHECKING as TC\n"
            "if TC:\n    pass\nelse:\n    cos.App",
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
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    from cosalette._app import App",
            "import typing\n"
            "if typing.TYPE_CHECKING:\n    from cosalette._app import App",
            "import typing as t\nif t.TYPE_CHECKING:\n    from cosalette import *",
            "from typing import TYPE_CHECKING as TC\nif TC:\n    from .. import *",
            "import cosalette\nfrom typing import TYPE_CHECKING as TC\n"
            "if TC:\n    cosalette.App",
            "import cosalette as cos; cos.Router()",
            "import other as cos; cos.App()",
            "from cosalette._router import *",
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
