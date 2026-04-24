"""Unit tests for per-device callable schedule= with name=callable telemetry.

Implements tests for the "Remaining Gap" described in
``triggerable-name-callable-proposal.md``:
  - ``schedule=callable`` accepted at decoration time when ``name=callable``
  - ``schedule_spec`` stored in ``_TelemetryRegistration``
  - ``_resolve_per_device_schedule`` resolves per-device CronSchedule
  - ``_expand_telemetry_names`` clears ``schedule_spec`` and sets ``schedule``
  - Error cases: callable schedule + interval, + group, wrong return type,
    missing config (static name), callable schedule registered with static name

Test Techniques Used:
- Specification-based Testing: verifying new schedule_spec field and resolver contracts
- Decision Table: schedule callable × (name callable / name str) × (interval / group)
- Error Guessing: invalid combinations and wrong return types from the callable
- Equivalence Partitioning: schedule callable returning str vs CronSchedule instance
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cosalette._app import App
from cosalette._cron import CronSchedule
from cosalette._registration import _TelemetryRegistration
from cosalette._settings import Settings
from cosalette._wiring import _expand_telemetry_names, _resolve_per_device_schedule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


@dataclass
class CalConfig:
    key: str
    cron: str = "0 0 * * * ?"


# ---------------------------------------------------------------------------
# _resolve_per_device_schedule unit tests
# ---------------------------------------------------------------------------


class TestResolvePerDeviceSchedule:
    """Unit tests for ``_resolve_per_device_schedule``.

    Technique: Specification-based + Equivalence Partitioning.
    """

    def _make_reg(
        self,
        *,
        schedule: CronSchedule | None = None,
        schedule_spec=None,
    ) -> _TelemetryRegistration:
        async def _dummy() -> dict[str, object]:
            return {}

        return _TelemetryRegistration(
            name="t",
            func=_dummy,
            injection_plan=[],
            interval=0.0,
            schedule=schedule,
            schedule_spec=schedule_spec,
        )

    def test_no_spec_returns_existing_schedule(self) -> None:
        """When schedule_spec is None, returns reg.schedule unchanged.

        Technique: Equivalence Partitioning — no-spec class.
        """
        sched = CronSchedule("0 */5 * * * ?")
        reg = self._make_reg(schedule=sched)

        result = _resolve_per_device_schedule(reg, "dev", CalConfig("dev"))

        assert result is sched

    def test_no_spec_no_schedule_returns_none(self) -> None:
        """When both schedule_spec and schedule are None, returns None."""
        reg = self._make_reg()

        result = _resolve_per_device_schedule(reg, "dev", None)

        assert result is None

    def test_spec_returning_str_parsed_to_cron_schedule(self) -> None:
        """schedule_spec returning a str is parsed to CronSchedule.

        Technique: Equivalence Partitioning — str return class.
        """
        spec = lambda cfg: cfg.cron  # noqa: E731
        reg = self._make_reg(schedule_spec=spec)
        config = CalConfig("dev", cron="0 0/15 * * * ?")

        result = _resolve_per_device_schedule(reg, "dev", config)

        assert isinstance(result, CronSchedule)
        assert result.expression == "0 0/15 * * * ?"

    def test_spec_returning_cron_schedule_passed_through(self) -> None:
        """schedule_spec returning CronSchedule is returned as-is.

        Technique: Equivalence Partitioning — CronSchedule return class.
        """
        sched = CronSchedule("0 0/15 * * * ?")
        spec = lambda cfg: sched  # noqa: E731
        reg = self._make_reg(schedule_spec=spec)

        result = _resolve_per_device_schedule(reg, "dev", CalConfig("dev"))

        assert result is sched

    def test_spec_with_none_config_raises(self) -> None:
        """schedule_spec with config=None (static name) raises ValueError.

        Technique: Error Guessing — callable schedule without per-device config.
        """
        spec = lambda cfg: "0 0 * * * ?"  # noqa: E731
        reg = self._make_reg(schedule_spec=spec)

        with pytest.raises(ValueError, match="config"):
            _resolve_per_device_schedule(reg, "dev", None)

    def test_spec_returning_invalid_type_raises(self) -> None:
        """schedule_spec returning wrong type raises TypeError.

        Technique: Error Guessing — bad return value.
        """
        spec = lambda cfg: 42  # noqa: E731
        reg = self._make_reg(schedule_spec=spec)

        with pytest.raises(TypeError, match="str or CronSchedule"):
            _resolve_per_device_schedule(reg, "dev", CalConfig("dev"))


# ---------------------------------------------------------------------------
# Registration — decorator path
# ---------------------------------------------------------------------------


class TestDecoratorCallableSchedule:
    """Registration via @app.telemetry() with schedule=callable.

    Technique: Specification-based — verify schedule_spec is stored correctly.
    """

    def test_callable_schedule_accepted_at_decoration_time(self, app: App) -> None:
        """schedule=callable with name=callable must not raise at registration time."""

        # Act — must not raise
        @app.telemetry(
            name=lambda s: {"cal-a": CalConfig("cal-a"), "cal-b": CalConfig("cal-b")},
            schedule=lambda cfg: cfg.cron,
        )
        async def handler() -> dict[str, object]:
            return {}

        # Assert — one entry stored, schedule_spec set, schedule=None
        assert len(app._telemetry) == 1
        reg = app._telemetry[0]
        assert reg.schedule_spec is not None
        assert reg.schedule is None
        assert reg.name_spec is not None

    def test_callable_schedule_stores_schedule_spec(self, app: App) -> None:
        """schedule= callable is stored as schedule_spec, not schedule.

        Technique: Specification-based — field assignment.
        """
        spec = lambda cfg: cfg.cron  # noqa: E731

        @app.telemetry(
            name=lambda s: {"x": CalConfig("x")},
            schedule=spec,
        )
        async def handler() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]
        assert reg.schedule_spec is spec

    def test_callable_schedule_sentinel_interval_stored(self, app: App) -> None:
        """When schedule=callable, interval sentinel 0.0 is stored.

        Technique: Decision Table — schedule-only row.
        """

        @app.telemetry(
            name=lambda s: {"x": CalConfig("x")},
            schedule=lambda cfg: "0 */5 * * * ?",
        )
        async def handler() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]
        assert reg.interval == 0.0

    def test_callable_schedule_with_interval_raises(self, app: App) -> None:
        """schedule=callable + interval= is mutually exclusive.

        Technique: Decision Table — both-present row.
        """
        with pytest.raises(ValueError, match="mutually exclusive"):

            @app.telemetry(
                name=lambda s: {"x": CalConfig("x")},
                interval=60,
                schedule=lambda cfg: "0 */5 * * * ?",
            )
            async def handler() -> dict[str, object]:
                return {}

    def test_callable_schedule_with_group_raises(self, app: App) -> None:
        """schedule=callable + group= is not allowed (groups require interval).

        Technique: Decision Table — schedule+group row.
        """
        with pytest.raises(ValueError, match="group"):

            @app.telemetry(
                name=lambda s: {"x": CalConfig("x")},
                schedule=lambda cfg: "0 */5 * * * ?",
                group="sensors",
            )
            async def handler() -> dict[str, object]:
                return {}


# ---------------------------------------------------------------------------
# Expansion — _expand_telemetry_names
# ---------------------------------------------------------------------------


class TestExpandTelemetryNamesWithScheduleSpec:
    """schedule_spec is resolved and cleared during name expansion.

    Technique: Specification-based + State Transition.
    """

    def test_schedule_spec_resolved_after_expansion(self, app: App) -> None:
        """After expansion, schedule is set and schedule_spec is cleared.

        Technique: State Transition — schedule_spec → resolved schedule.
        """
        cron_expr = "0 0/30 * * * ?"
        spec = lambda cfg: cron_expr  # noqa: E731

        @app.telemetry(
            name=lambda s: {"dev-a": CalConfig("dev-a"), "dev-b": CalConfig("dev-b")},
            schedule=spec,
        )
        async def handler() -> dict[str, object]:
            return {}

        _expand_telemetry_names(app._telemetry, Settings())

        assert len(app._telemetry) == 2
        for reg in app._telemetry:
            assert reg.schedule_spec is None
            assert isinstance(reg.schedule, CronSchedule)

    def test_per_device_schedule_different_per_device(self, app: App) -> None:
        """Each device gets its own CronSchedule from the callable.

        Technique: Specification-based — per-device resolution.
        """

        @app.telemetry(
            name=lambda s: {
                "hourly": CalConfig("hourly", cron="0 0 * * * ?"),
                "daily": CalConfig("daily", cron="0 0 0 * * ?"),
            },
            schedule=lambda cfg: cfg.cron,
        )
        async def handler() -> dict[str, object]:
            return {}

        _expand_telemetry_names(app._telemetry, Settings())

        regs = {r.name: r for r in app._telemetry}
        assert regs["hourly"].schedule is not None
        assert regs["daily"].schedule is not None
        # Both are CronSchedule instances; their expressions differ
        assert regs["hourly"].schedule is not regs["daily"].schedule

    def test_expansion_preserves_other_fields(self, app: App) -> None:
        """Expansion retains triggerable, name, per_device_config.

        Technique: Specification-based — field preservation.
        """

        @app.telemetry(
            name=lambda s: {"x": CalConfig("x")},
            schedule=lambda cfg: "0 */5 * * * ?",
            triggerable=True,
        )
        async def handler() -> dict[str, object]:
            return {}

        _expand_telemetry_names(app._telemetry, Settings())

        reg = app._telemetry[0]
        assert reg.name == "x"
        assert reg.triggerable is True
        assert reg.per_device_config is not None
        assert reg.name_spec is None
        assert reg.schedule_spec is None

    def test_schedule_spec_with_cron_schedule_return(self, app: App) -> None:
        """schedule_spec returning CronSchedule directly is stored as-is.

        Technique: Equivalence Partitioning — CronSchedule return class.
        """
        fixed_sched = CronSchedule("0 0 12 * * ?")

        @app.telemetry(
            name=lambda s: {"noon": CalConfig("noon")},
            schedule=lambda cfg: fixed_sched,
        )
        async def handler() -> dict[str, object]:
            return {}

        _expand_telemetry_names(app._telemetry, Settings())

        assert app._telemetry[0].schedule is fixed_sched


# ---------------------------------------------------------------------------
# Validation — rejection of invalid combinations
# ---------------------------------------------------------------------------


class TestCallableScheduleValidation:
    """Validate that forbidden schedule_spec combinations raise early.

    Technique: Decision Table — static name × callable schedule,
    and schedule_spec incompatible with group= / schedule=.
    """

    def test_callable_schedule_with_static_name_raises(self, app: App) -> None:
        """schedule=callable with a static name= string must raise ValueError.

        Technique: Decision Table — callable schedule × static name row.
        Static names have no per-device config; the spec can never be resolved.
        """
        with pytest.raises(ValueError, match="name=.*callable|callable.*name="):

            @app.telemetry(
                name="sensor",
                schedule=lambda cfg: "0 */5 * * * ?",
            )
            async def handler() -> dict[str, object]:
                return {}

    def test_callable_schedule_with_static_name_via_add_telemetry_raises(
        self, app: App
    ) -> None:
        """add_telemetry with schedule_spec + static name must raise ValueError.

        Covers the imperative path (Copilot review finding).
        """

        async def handler() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="callable"):
            app.add_telemetry(
                "sensor",
                handler,
                interval=60,
                schedule_spec=lambda cfg: "0 */5 * * * ?",
            )

    def test_schedule_spec_resolves_when_name_spec_is_none(self, app: App) -> None:
        """A registration with schedule_spec but name_spec=None is skipped by expansion.

        _expand_telemetry_names only processes entries where name_spec is set.
        An entry with name_spec=None but schedule_spec set should not survive
        registration (caught by validation), but if one exists in the list it
        should pass through unexpanded without error — the schedule_spec is
        never resolved.
        """

        # Bypass App registration by constructing a raw registration.
        async def _dummy() -> dict[str, object]:
            return {}

        reg = _TelemetryRegistration(
            name="static-dev",
            func=_dummy,
            injection_plan=[],
            interval=60.0,
            schedule=None,
            schedule_spec=lambda cfg: "0 * * * * ?",
            name_spec=None,  # no expansion — schedule_spec never invoked
        )
        telemetry: list[_TelemetryRegistration] = [reg]
        # Expansion must not raise; entry with name_spec=None passes through
        _expand_telemetry_names(telemetry, Settings())
        assert len(telemetry) == 1
        # schedule_spec is still set — it was never resolved (no name_spec)
        assert telemetry[0].schedule_spec is not None

    def test_spec_returning_invalid_cron_string_raises(self) -> None:
        """schedule_spec returning an invalid cron string propagates the parse error.

        Technique: Error Guessing — bad cron expression from callable.
        CronSchedule raises ValueError for malformed expressions.
        """

        async def _dummy() -> dict[str, object]:
            return {}

        reg = _TelemetryRegistration(
            name="t",
            func=_dummy,
            injection_plan=[],
            interval=0.0,
            schedule_spec=lambda cfg: "not a cron expression at all",
        )
        with pytest.raises(ValueError):
            _resolve_per_device_schedule(reg, "t", CalConfig("t"))
