"""Unit tests for the opt-in health file, its probe and exit_after_stale.

ADR-083: with ``COSALETTE_HEALTH_FILE`` set, the app writes its heartbeat
payload atomically to that path; ``<app> health`` / ``cosalette health``
exit 0 or 1 from it; ``App(exit_after_stale=)`` ends the app with exit code
5 once a telemetry entity has stayed stale that long.

Test Techniques Used:
    - Equivalence Partitioning: probe outcomes (fine, missing, unreadable,
      not an object, no timestamp, too old, failing status)
    - Boundary Value Analysis: file age at and just over --max-age; stale
      time at and just under exit_after_stale; invalid exit_after_stale
    - Decision Table Testing: device status x --fail-on set
    - State Transition Testing: write failure warns once, then DEBUG
    - Error Guessing: leftover temporary files, unset environment variable
    - Specification-based Testing: exit-code mapping in both CLIs
    - Mock-based Isolation: MockMqttClient, FakeClock and ManualClock
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cosalette import App
from cosalette._cli import _run_app, build_cli
from cosalette._constants import EXIT_STALE
from cosalette._health import HealthReporter
from cosalette._health._liveness import (
    HEALTH_FILE_ENV,
    HealthFileWriter,
    StaleTelemetryError,
    check_health_file,
    health_file_from_env,
)
from cosalette._package_cli import app as package_cli
from cosalette._supervisor import TaskSupervisor
from cosalette._wiring._task_lifecycle import freshness_loop, start_health_file_task
from cosalette.testing import (
    AppHarness,
    FakeClock,
    ManualClock,
    MockMqttClient,
    make_settings,
)

pytestmark = pytest.mark.unit

NOW = 1_000_000.0


@pytest.fixture
def reporter() -> HealthReporter:
    clock = FakeClock()
    clock._time = 0.0
    return HealthReporter(
        mqtt=MockMqttClient(), topic_prefix="myapp", version="1.0.0", clock=clock
    )


def _write(path: Path, **data: Any) -> Path:
    base: dict[str, Any] = {"status": "online", "devices": {}, "interval": 60.0}
    base.update(data)
    path.write_text(json.dumps(base), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class TestHealthFileWriter:
    """The writer produces the heartbeat plus write metadata, atomically."""

    def test_writes_heartbeat_with_metadata(
        self, tmp_path: Path, reporter: HealthReporter
    ) -> None:
        # Arrange
        reporter.set_device_status("sensor", "error")
        path = tmp_path / "health.json"
        writer = HealthFileWriter(
            path=path, reporter=reporter, interval=30.0, wall_clock=lambda: NOW
        )

        # Act
        ok = writer.write()

        # Assert
        data = json.loads(path.read_text())
        assert ok is True
        assert data["written_at"] == NOW
        assert data["interval"] == 30.0
        assert data["devices"]["sensor"]["status"] == "error"
        assert data["version"] == "1.0.0"

    def test_leaves_no_temporary_files(
        self, tmp_path: Path, reporter: HealthReporter
    ) -> None:
        writer = HealthFileWriter(
            path=tmp_path / "health.json", reporter=reporter, interval=60.0
        )

        writer.write()
        writer.write()

        assert [p.name for p in tmp_path.iterdir()] == ["health.json"]

    def test_respects_include_version(
        self, tmp_path: Path, reporter: HealthReporter
    ) -> None:
        reporter.include_version = False
        path = tmp_path / "health.json"

        HealthFileWriter(path=path, reporter=reporter, interval=60.0).write()

        assert "version" not in json.loads(path.read_text())

    def test_failed_write_warns_once_then_debug(
        self,
        tmp_path: Path,
        reporter: HealthReporter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Arrange: the directory does not exist, like a read-only mount
        writer = HealthFileWriter(
            path=tmp_path / "missing" / "health.json",
            reporter=reporter,
            interval=60.0,
        )

        # Act
        with caplog.at_level(logging.DEBUG, logger="cosalette._health._liveness"):
            results = [writer.write(), writer.write(), writer.write()]

        # Assert
        assert results == [False, False, False]
        assert [r.levelname for r in caplog.records] == ["WARNING", "DEBUG", "DEBUG"]

    def test_remove_tolerates_missing_file(
        self, tmp_path: Path, reporter: HealthReporter
    ) -> None:
        path = tmp_path / "health.json"
        writer = HealthFileWriter(path=path, reporter=reporter, interval=60.0)
        writer.write()

        writer.remove()
        writer.remove()

        assert not path.exists()


class TestHealthFileFromEnv:
    """The file is off unless the environment variable names a path."""

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_or_blank_is_off(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        if value is None:
            monkeypatch.delenv(HEALTH_FILE_ENV, raising=False)
        else:
            monkeypatch.setenv(HEALTH_FILE_ENV, value)

        assert health_file_from_env() is None

    def test_path_is_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HEALTH_FILE_ENV, "/run/app/health.json")

        assert health_file_from_env() == Path("/run/app/health.json")

    def test_no_task_when_off(self, reporter: HealthReporter) -> None:
        assert start_health_file_task(None, 60.0, reporter) is None

    async def test_task_writes_immediately_with_default_interval(
        self, tmp_path: Path, reporter: HealthReporter
    ) -> None:
        # Arrange: heartbeats disabled, so the 60 s default applies
        path = tmp_path / "health.json"

        # Act
        started = start_health_file_task(path, None, reporter)
        assert started is not None
        writer, task = started
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Assert
        assert writer.interval == 60.0
        assert json.loads(path.read_text())["interval"] == 60.0


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


class TestCheckHealthFile:
    """The probe judges age and device statuses."""

    def test_fresh_file_is_healthy(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "h.json", written_at=NOW - 10)

        result = check_health_file(path, now=NOW)

        assert result.healthy is True

    def test_missing_file(self, tmp_path: Path) -> None:
        result = check_health_file(tmp_path / "nope.json", now=NOW)

        assert result.healthy is False
        assert "does not exist" in result.reason

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            ("{not json", "unreadable"),
            ("[1, 2]", "not a JSON object"),
            ('{"devices": {}}', "no written_at"),
            ('{"written_at": true}', "no written_at"),
        ],
    )
    def test_malformed_file(self, tmp_path: Path, content: str, reason: str) -> None:
        path = tmp_path / "h.json"
        path.write_text(content)

        result = check_health_file(path, now=NOW)

        assert result.healthy is False
        assert reason in result.reason

    @pytest.mark.parametrize(
        ("age", "healthy"), [(180.0, True), (180.5, False)], ids=["at", "over"]
    )
    def test_default_max_age_is_three_intervals(
        self, tmp_path: Path, age: float, healthy: bool
    ) -> None:
        path = _write(tmp_path / "h.json", written_at=NOW - age, interval=60.0)

        result = check_health_file(path, now=NOW)

        assert result.healthy is healthy

    def test_explicit_max_age_wins(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "h.json", written_at=NOW - 20, interval=60.0)

        result = check_health_file(path, now=NOW, max_age=10.0)

        assert result.healthy is False
        assert "20s old" in result.reason

    def test_invalid_interval_falls_back_to_sixty(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "h.json", written_at=NOW - 170, interval=-1)

        assert check_health_file(path, now=NOW).healthy is True

    @pytest.mark.parametrize(
        ("status", "fail_on", "healthy"),
        [
            ("stale", ("stale",), False),
            ("error", ("stale",), True),
            ("error", ("stale", "error"), False),
            ("ok", ("stale", "error"), True),
        ],
    )
    def test_device_status_against_fail_on(
        self, tmp_path: Path, status: str, fail_on: tuple[str, ...], healthy: bool
    ) -> None:
        path = _write(
            tmp_path / "h.json",
            written_at=NOW,
            devices={"radon": {"status": status}, "odd": "not-a-dict"},
        )

        result = check_health_file(path, now=NOW, fail_on=fail_on)

        assert result.healthy is healthy
        if not healthy:
            assert f"radon={status}" in result.reason


class TestHealthCommand:
    """Both CLIs exit 0 (healthy) or 1 (unhealthy), per Docker's contract."""

    @pytest.fixture(params=["app", "package"])
    def cli(self, request: pytest.FixtureRequest) -> Any:
        if request.param == "app":
            return build_cli(App("probeapp", version="1.0.0"))
        return package_cli

    def test_healthy_file_exits_zero(self, cli: Any, tmp_path: Path) -> None:
        import time

        path = _write(tmp_path / "h.json", written_at=time.time())

        result = CliRunner().invoke(cli, ["health", "--file", str(path)])

        assert result.exit_code == 0
        assert "healthy" in result.output

    def test_env_var_supplies_the_file(self, cli: Any, tmp_path: Path) -> None:
        import time

        path = _write(
            tmp_path / "h.json",
            written_at=time.time(),
            devices={"radon": {"status": "stale"}},
        )

        result = CliRunner().invoke(cli, ["health"], env={HEALTH_FILE_ENV: str(path)})

        assert result.exit_code == 1
        assert "radon=stale" in result.output

    def test_fail_on_is_repeatable(self, cli: Any, tmp_path: Path) -> None:
        import time

        path = _write(
            tmp_path / "h.json",
            written_at=time.time(),
            devices={"radon": {"status": "error"}},
        )

        result = CliRunner().invoke(
            cli,
            ["health", "--file", str(path), "--fail-on", "stale", "--fail-on", "error"],
        )

        assert result.exit_code == 1

    def test_missing_file_exits_one(self, cli: Any, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli, ["health", "--file", str(tmp_path / "nope.json")]
        )

        assert result.exit_code == 1

    def test_no_file_configured_exits_one(self, cli: Any) -> None:
        result = CliRunner().invoke(cli, ["health"], env={HEALTH_FILE_ENV: ""})

        assert result.exit_code == 1
        assert HEALTH_FILE_ENV in result.output

    def test_too_old_exits_one(self, cli: Any, tmp_path: Path) -> None:
        path = _write(tmp_path / "h.json", written_at=1.0)

        result = CliRunner().invoke(cli, ["health", "--file", str(path)])

        assert result.exit_code == 1
        assert "old" in result.output


class TestAppCallbackSkipsSubcommands:
    """A subcommand runs instead of the app (ADR-083)."""

    def test_health_does_not_start_the_app(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        started: list[object] = []
        monkeypatch.setattr(
            "cosalette._cli._run_app", lambda app, settings: started.append(app)
        )
        cli = build_cli(App("probeapp", version="1.0.0"))

        # Act
        CliRunner().invoke(cli, ["health", "--file", str(tmp_path / "x.json")])

        # Assert
        assert started == []


# ---------------------------------------------------------------------------
# exit_after_stale
# ---------------------------------------------------------------------------


class TestExitAfterStaleParameter:
    """The App parameter is validated like the other intervals."""

    def test_default_is_none(self) -> None:
        assert App("x")._exit_after_stale is None

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
    def test_rejects_non_positive(self, bad: float) -> None:
        with pytest.raises(ValueError, match="exit_after_stale must be positive"):
            App("x", exit_after_stale=bad)


class TestLongestStale:
    """Stale time counts from the moment the entity crossed stale_after."""

    async def test_none_while_fresh(self, reporter: HealthReporter) -> None:
        reporter.track_freshness("a", 30.0)

        await reporter.check_freshness()

        assert reporter.longest_stale() is None

    async def test_reports_the_longest(self, reporter: HealthReporter) -> None:
        # Arrange
        clock = reporter.clock
        assert isinstance(clock, FakeClock)
        reporter.track_freshness("short", 50.0)
        reporter.track_freshness("long", 30.0)
        reporter.track_freshness("untracked", None)

        # Act
        clock._time = 100.0
        await reporter.check_freshness()

        # Assert
        assert reporter.longest_stale() == ("long", 70.0)


class TestFreshnessLoopExit:
    """The watchdog requests the exit once, at the configured stale time."""

    @pytest.mark.parametrize(
        ("exit_after", "steps", "calls_expected"),
        [(30.0, 4, 1), (30.5, 4, 0), (10.0, 8, 1)],
        ids=["at", "under", "only-once"],
    )
    async def test_exit_boundary(
        self, exit_after: float, steps: int, calls_expected: int
    ) -> None:
        # Arrange: stale_after=10, so stale for 30 s at the t=40 check
        clock = ManualClock()
        reporter = HealthReporter(
            mqtt=MockMqttClient(), topic_prefix="p", version="1", clock=clock
        )
        reporter.track_freshness("radon", 10.0)
        calls: list[StaleTelemetryError] = []
        task = asyncio.create_task(
            freshness_loop(
                reporter,
                10.0,
                exit_after_stale=exit_after,
                on_stale_exit=calls.append,
            )
        )

        # Act
        for _ in range(steps):
            await clock.advance(10.0)
        await clock.settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Assert: later checks that still see it stale do not call again
        assert len(calls) == calls_expected
        if calls:
            assert calls[0].entity == "radon"
            assert calls[0].stale_for >= exit_after

    async def test_stale_exit_progresses_while_restart_is_blocked(self) -> None:
        clock = ManualClock()
        reporter = HealthReporter(
            mqtt=MockMqttClient(), topic_prefix="p", version="1", clock=clock
        )
        reporter.track_freshness("radon", 10.0)
        restart_started = asyncio.Event()
        exits: list[StaleTelemetryError] = []

        async def blocked_restart(_names: list[str]) -> None:
            restart_started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            freshness_loop(
                reporter,
                10.0,
                exit_after_stale=20.0,
                on_stale_exit=exits.append,
                on_newly_stale=blocked_restart,
            )
        )

        await clock.advance(10.0)
        await clock.advance(10.0)
        await clock.settle()
        assert restart_started.is_set()
        await clock.advance(10.0)
        await clock.settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(exits) == 1
        assert exits[0].entity == "radon"


class TestStaleExitMapping:
    """request_exit ends the app; the CLI maps the error to exit code 5."""

    def test_request_exit_sets_error_and_shutdown(self) -> None:
        event = asyncio.Event()
        supervisor = TaskSupervisor(clock=FakeClock(), shutdown_event=event)
        error = StaleTelemetryError("radon", 60.0)

        supervisor.request_exit(error)
        supervisor.request_exit(StaleTelemetryError("other", 1.0))

        assert supervisor.exit_error is error
        assert supervisor.fatal_error is None
        assert event.is_set()

    def test_run_app_exits_with_stale_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = App("x")

        async def _raise(**_: object) -> None:
            raise StaleTelemetryError("radon", 60.0)

        monkeypatch.setattr(app, "_run_async", _raise)

        with pytest.raises(SystemExit) as exc_info:
            _run_app(app, make_settings())

        assert exc_info.value.code == EXIT_STALE


class TestEndToEnd:
    """The health file and exit_after_stale run inside the app lifecycle."""

    async def test_stale_entity_ends_the_run(self) -> None:
        # Arrange
        clock = ManualClock()
        harness = AppHarness(
            app=App("testapp", exit_after_stale=30.0),
            mqtt=MockMqttClient(),
            clock=clock,
            settings=make_settings(),
            shutdown_event=asyncio.Event(),
        )

        @harness.app.telemetry("sensor", interval=10, stale_after=20.0)
        async def sensor() -> dict[str, int]:
            raise ValueError("bad parse")

        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count("testapp/sensor/availability", 1)

        # Act: stale at t=20, exit due at t=50
        for _ in range(8):
            if task.done():
                break
            await harness.advance_time(10.0)

        # Assert
        with pytest.raises(StaleTelemetryError, match="sensor"):
            await asyncio.wait_for(task, timeout=5)

    async def test_health_file_written_during_run_and_removed_after(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Arrange
        path = tmp_path / "health.json"
        monkeypatch.setenv(HEALTH_FILE_ENV, str(path))
        harness = AppHarness.create(clock=ManualClock())

        @harness.app.telemetry("sensor", interval=10)
        async def sensor() -> dict[str, int]:
            return {"value": 1}

        # Act
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count("testapp/sensor/availability", 1)
        during = json.loads(path.read_text())
        harness.trigger_shutdown()
        await task

        # Assert
        assert during["interval"] == 60.0
        assert "written_at" in during
        assert not path.exists()
