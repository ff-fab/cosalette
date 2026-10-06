"""The CLI works with rich, pygments and markdown-it-py removed.

Test Techniques Used:
    - Error Guessing: an image that deletes rich (or only pygments, which
      ``rich.syntax`` imports) used to crash on ``--help`` and usage errors.
    - Equivalence Partitioning: help, a usage error and a subcommand's help,
      for an app CLI and the ``cosalette`` package CLI.

See Also:
    cos-8jxg.6 — plain help fallback; ADR-005 amendment.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from cosalette._utils import _typer_options

pytestmark = pytest.mark.unit

_APP_CLI = """
import cosalette
app = cosalette.App(name="demo", version="1.0", description="Demo")
app.cli()
"""

_PACKAGE_CLI = """
from cosalette._package_cli import app
app()
"""


@pytest.mark.parametrize("name", ["rich", "pygments", "markdown_it"])
def test_blocked_module_selects_plain_typer_options(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """find_spec recognises sys.modules entries that block imports."""
    _typer_options.cache_clear()
    try:
        monkeypatch.setitem(sys.modules, name, None)
        assert _typer_options() == {
            "rich_markup_mode": None,
            "pretty_exceptions_enable": False,
        }
    finally:
        _typer_options.cache_clear()


def _run(
    entry: str, missing: tuple[str, ...], *args: str
) -> subprocess.CompletedProcess[str]:
    block = "".join(f"sys.modules[{name!r}] = None\n" for name in missing)
    script = f"import sys\n{block}sys.argv[0] = 'demo'\n{entry}"
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "missing",
    [("rich", "pygments", "markdown_it"), ("pygments",)],
    ids=["all-removed", "pygments-removed"],
)
class TestCliWithoutRich:
    def test_app_help_is_plain(self, missing: tuple[str, ...]) -> None:
        result = _run(_APP_CLI, missing, "--help")

        assert result.returncode == 0, result.stderr
        assert "demo v1.0" in result.stdout
        assert "--dry-run" in result.stdout

    def test_usage_error_exits_2(self, missing: tuple[str, ...]) -> None:
        result = _run(_APP_CLI, missing, "--nope")

        assert result.returncode == 2
        assert "No such option: --nope" in result.stderr

    @pytest.mark.parametrize("command", ["schema", "health"])
    def test_subcommand_help(self, missing: tuple[str, ...], command: str) -> None:
        result = _run(_APP_CLI, missing, command, "--help")

        assert result.returncode == 0, result.stderr
        assert "Usage:" in result.stdout

    def test_package_cli_help(self, missing: tuple[str, ...]) -> None:
        result = _run(_PACKAGE_CLI, missing, "--help")

        assert result.returncode == 0, result.stderr
        assert "IoT-to-MQTT framework CLI" in result.stdout
