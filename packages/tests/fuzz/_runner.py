"""Shared atheris/libFuzzer bootstrap for the security:fuzz harnesses.

Each ``fuzz_*.py`` harness enables Atheris import instrumentation around
the imports under test, defines a crash-oracle target, then delegates to
:func:`run` here — keeping the libFuzzer setup boilerplate in one place
(duplication gate).

Run via ``task security:fuzz`` (per-harness budget and CLI passthrough)
or directly::

    uv run --group fuzz python packages/tests/fuzz/fuzz_json.py -runs=100000
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from typing import Any

# Loaded dynamically so type checking stays deterministic in environments
# without the `fuzz` dependency group (atheris wheels are Linux/macOS only).
atheris = importlib.import_module("atheris")

#: Re-exported so harnesses never import atheris directly.
FuzzedDataProvider = atheris.FuzzedDataProvider


def instrument_imports() -> Any:
    """Return the Atheris import hook manager (use as a ``with`` context).

    Modules imported inside the ``with`` block get coverage instrumentation,
    which is what turns libFuzzer into a coverage-guided fuzzer for the
    pure-Python parsers instead of a blind byte mutator.
    """
    return atheris.instrument_imports()


def run(target: Callable[[bytes], None]) -> None:
    """Run *target* under libFuzzer, forwarding CLI arguments.

    Useful flags: ``-runs=N`` (fixed budget), ``-max_total_time=S`` (time
    budget), ``-timeout=S`` (per-input hang bound), ``-seed=N``.
    """
    atheris.Setup(sys.argv, target)
    atheris.Fuzz()
