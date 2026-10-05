"""Unit tests for the lazy (PEP 562) public exports of ``cosalette``.

Test Techniques Used:
    - Specification-based Testing: every ``__all__`` name resolves to the object
      defined in its source module; ``dir()`` lists the public API.
    - Error Guessing: unknown names raise the standard ``AttributeError``; the
      runtime export map drifting from the ``TYPE_CHECKING`` imports.
    - Equivalence Partitioning: a fresh interpreter importing the package or the
      liveness module (lightweight entry points) loads no heavy dependency.

See Also:
    cos-ht8o.4 — lazy public exports keep ``import cosalette`` cheap.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import cosalette
import cosalette._health

pytestmark = pytest.mark.unit

HEAVY_MODULES = {"pydantic", "typer", "aiomqtt", "cosalette._app"}


class TestLazyExports:
    """Lazy names behave exactly like the former eager re-exports."""

    @pytest.mark.parametrize(
        ("module", "name"),
        [
            (module, name)
            for module, names in cosalette._EXPORTS.items()
            for name in names
        ],
    )
    def test_export_is_identical_to_source_object(self, module: str, name: str) -> None:
        """Technique: Specification-based — re-export is the canonical object."""
        assert getattr(cosalette, name) is getattr(
            importlib.import_module(module), name
        )

    def test_export_map_covers_all(self) -> None:
        """Technique: Specification-based — ``__all__`` and the map agree."""
        mapped = {name for names in cosalette._EXPORTS.values() for name in names}

        assert mapped | {"__version__"} == set(cosalette.__all__)

    @pytest.mark.parametrize("package", [cosalette, cosalette._health])
    def test_type_checking_imports_match_export_map(self, package: ModuleType) -> None:
        """Technique: Error Guessing — static imports drift from the runtime map."""
        # Arrange
        tree = ast.parse(inspect.getsource(package))
        guard = next(n for n in tree.body if isinstance(n, ast.If))
        static = {
            alias.name
            for node in guard.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        # Act
        public = set(package.__all__) - {"__version__"}

        # Assert
        assert static == public

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        """Technique: Error Guessing — standard message, no import attempted."""
        with pytest.raises(
            AttributeError, match="module 'cosalette' has no attribute 'Nope'"
        ):
            _ = cosalette.Nope  # type: ignore[attr-defined]

    def test_dir_lists_public_api(self) -> None:
        """Technique: Specification-based — ``dir()`` covers ``__all__``."""
        assert set(cosalette.__all__) <= set(dir(cosalette))

    def test_star_import_binds_all_names(self) -> None:
        """Technique: Specification-based — ``from cosalette import *`` works."""
        namespace: dict[str, object] = {}

        exec("from cosalette import *", namespace)

        assert set(cosalette.__all__) <= namespace.keys()


class TestImportCost:
    """Lightweight entry points leave the heavy framework unloaded."""

    @pytest.mark.parametrize(
        ("statement", "forbidden"),
        [
            ("import cosalette", HEAVY_MODULES | {"orjson"}),
            # The liveness module serialises the health file through orjson.
            ("import cosalette._health._liveness", HEAVY_MODULES),
            # The cosalette-health fallback probe is stdlib-only (ADR-087).
            ("import cosalette._health._probe", HEAVY_MODULES | {"orjson"}),
        ],
    )
    def test_import_does_not_load_heavy_modules(
        self, statement: str, forbidden: set[str]
    ) -> None:
        """Technique: Equivalence Partitioning — fresh interpreter per entry point."""
        # Arrange
        script = f"import sys; {statement}; print(*sorted(sys.modules), sep='\\n')"

        # Act
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )

        # Assert
        loaded = set(result.stdout.split())
        assert loaded.isdisjoint(forbidden)


#: Subsystems a telemetry-only app never needs at run time (cos-8jxg.5).
DEFERRED_SUBSYSTEMS = {
    "cosalette._cron",
    "cosalette._strategies",
    "cosalette._runners._command_runner",
    "cosalette._runners._stream_runner",
    "cosalette._schema._consumer_gen",
    "cosalette._schema._loader",
    "typer",
}

_TELEMETRY_APP = """
import asyncio, sys
import cosalette
from cosalette._mqtt import MockMqttClient
from cosalette._settings import Settings

app = cosalette.App(name="demo", version="1.0")

@app.telemetry("temp", interval=0.01)
async def temp() -> dict[str, float]:
    return {"c": 1.0}

async def main() -> None:
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(0.1, stop.set)
    mqtt = MockMqttClient()
    await app._run_async(mqtt=mqtt, settings=Settings(), shutdown_event=stop)

asyncio.run(main())
print(*sorted(sys.modules), sep="\\n")
"""


def test_running_telemetry_app_skips_unused_subsystems(tmp_path: Path) -> None:
    """Technique: Equivalence Partitioning — a running telemetry-only app.

    Pins the subsystems that load on first use, so an eager import does not
    creep back into the run path unnoticed.
    """
    # Act
    result = subprocess.run(
        [sys.executable, "-c", _TELEMETRY_APP],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )

    # Assert
    loaded = set(result.stdout.split())
    assert "cosalette._runners._telemetry_runner" in loaded
    assert loaded.isdisjoint(DEFERRED_SUBSYSTEMS)
