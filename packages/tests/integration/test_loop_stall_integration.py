"""Integration test — a blocked event loop exits the process with code 6.

Runs a real app in a subprocess with ``COSALETTE_LOOP_STALL_TIMEOUT`` set
and a telemetry handler that blocks the loop with :func:`time.sleep`.  The
watchdog thread must dump every thread's stack to stderr and exit with
:data:`EXIT_LOOP_STALL` (ADR-088), long before the handler would return.

Test Techniques Used:
    - Integration Testing: env var -> App.run -> watchdog -> os._exit.
    - Specification-based Testing: exit code and stderr stack dump.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cosalette._constants import EXIT_LOOP_STALL
from cosalette._health._loop_stall import LOOP_STALL_ENV

pytestmark = pytest.mark.integration

_SCRIPT = textwrap.dedent(
    """
    import time

    from cosalette import App
    from cosalette.testing import MockMqttClient, make_settings

    app = App("stallapp", version="1.0.0")

    @app.telemetry("sensor", interval=60)
    async def sensor() -> dict[str, int]:
        time.sleep(30)  # blocks the event loop
        return {"value": 1}

    app.run(mqtt=MockMqttClient(), settings=make_settings())
    """
)


def test_blocked_loop_exits_with_loop_stall_code(tmp_path: Path) -> None:
    # Arrange
    env = {**os.environ, LOOP_STALL_ENV: "0.5"}

    # Act
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=20,
        check=False,
    )

    # Assert
    assert result.returncode == EXIT_LOOP_STALL, result.stderr
    assert "CRITICAL cosalette: event loop stalled" in result.stderr
    assert "most recent call first" in result.stderr
    assert "in sensor" in result.stderr
