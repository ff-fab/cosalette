"""Tests for telemetry coalescing groups scheduler.

Test Techniques Used:
    - Integration Testing: Full _run_async lifecycle with grouped handlers
    - Async Coordination: asyncio.Event for deterministic test control
    - Mock-based Isolation: MockMqttClient + FakeClock avoid real I/O
    - Error Isolation: Verify handler errors don't crash the group
    - Batch Verification: Verify correct batching at coinciding ticks
"""

from __future__ import annotations

import asyncio

import pytest

from cosalette._app import App
from cosalette._strategies import OnChange
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

# mock_mqtt and fake_clock fixtures provided by cosalette.testing._plugin


class _BadInit:
    """Sentinel returned by a failing init function (module-level for PEP 563)."""


# ---------------------------------------------------------------------------
# TestGroupSchedulerBasic
# ---------------------------------------------------------------------------


class TestGroupSchedulerBasic:
    """Basic scheduler behaviour for grouped telemetry handlers.

    Technique: Integration Testing — verify that grouped handlers fire
    and publish via the shared tick-aligned scheduler, and that
    ungrouped handlers remain unaffected.
    """

    async def test_single_handler_in_group(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """One grouped handler fires and publishes like ungrouped."""
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        @app.telemetry(name="temp", interval=0.01, group="g")
        async def temp() -> dict[str, object]:
            called.set()
            return {"celsius": 22.5}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert called.is_set()
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) >= 1

    async def test_two_handlers_same_interval(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Two grouped handlers with the same interval both fire and publish."""
        app = App(name="testapp", version="1.0.0")
        a_called = asyncio.Event()
        b_called = asyncio.Event()

        @app.telemetry(name="sensor_a", interval=0.01, group="g")
        async def sensor_a() -> dict[str, object]:
            a_called.set()
            return {"a": 1}

        @app.telemetry(name="sensor_b", interval=0.01, group="g")
        async def sensor_b() -> dict[str, object]:
            b_called.set()
            return {"b": 2}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await a_called.wait()
            await b_called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert a_called.is_set()
        assert b_called.is_set()
        assert len(mock_mqtt.get_messages_for("testapp/sensor_a/state")) >= 1
        assert len(mock_mqtt.get_messages_for("testapp/sensor_b/state")) >= 1

    async def test_ungrouped_handlers_still_independent(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Ungrouped handlers run independently alongside grouped ones."""
        app = App(name="testapp", version="1.0.0")
        grouped_called = asyncio.Event()
        ungrouped_called = asyncio.Event()

        @app.telemetry(name="grouped", interval=0.01, group="g")
        async def grouped() -> dict[str, object]:
            grouped_called.set()
            return {"g": 1}

        @app.telemetry(name="ungrouped", interval=0.01)
        async def ungrouped() -> dict[str, object]:
            ungrouped_called.set()
            return {"u": 2}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await grouped_called.wait()
            await ungrouped_called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert grouped_called.is_set()
        assert ungrouped_called.is_set()
        assert len(mock_mqtt.get_messages_for("testapp/grouped/state")) >= 1
        assert len(mock_mqtt.get_messages_for("testapp/ungrouped/state")) >= 1


# ---------------------------------------------------------------------------
# TestGroupSchedulerBatching
# ---------------------------------------------------------------------------


class TestGroupSchedulerBatching:
    """Batch execution semantics for coinciding tick times.

    Technique: Integration Testing — verify that handlers with
    coinciding fire times are batched and executed in registration
    order.
    """

    async def test_handlers_different_intervals_batch_at_t0(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handlers with different intervals both fire at t=0."""
        app = App(name="testapp", version="1.0.0")
        call_order: list[str] = []
        both_called = asyncio.Event()

        @app.telemetry(name="fast", interval=0.02, group="g")
        async def fast() -> dict[str, object]:
            call_order.append("fast")
            if len(call_order) >= 2:
                both_called.set()
            return {"f": 1}

        @app.telemetry(name="slow", interval=0.04, group="g")
        async def slow() -> dict[str, object]:
            call_order.append("slow")
            if len(call_order) >= 2:
                both_called.set()
            return {"s": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await both_called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Both should have been called (at least at t=0)
        assert "fast" in call_order
        assert "slow" in call_order

    async def test_registration_order_preserved_in_batch(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handlers execute in registration order within a batch."""
        app = App(name="testapp", version="1.0.0")
        call_order: list[str] = []
        batch_done = asyncio.Event()

        @app.telemetry(name="alpha", interval=0.01, group="g")
        async def alpha() -> dict[str, object]:
            call_order.append("alpha")
            return {"a": 1}

        @app.telemetry(name="beta", interval=0.01, group="g")
        async def beta() -> dict[str, object]:
            call_order.append("beta")
            if len(call_order) >= 2:
                batch_done.set()
            return {"b": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await batch_done.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # First batch at t=0 should be [alpha, beta] in registration order
        assert call_order[0] == "alpha"
        assert call_order[1] == "beta"


# ---------------------------------------------------------------------------
# TestGroupSchedulerErrorIsolation
# ---------------------------------------------------------------------------


class TestGroupSchedulerErrorIsolation:
    """Error isolation within a coalescing group.

    Technique: Error Isolation — verify that errors in one handler
    do not propagate to or prevent execution of sibling handlers.
    """

    async def test_handler_error_does_not_crash_group(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Error in one handler doesn't prevent others from executing."""
        app = App(name="testapp", version="1.0.0")
        b_called = asyncio.Event()

        @app.telemetry(name="a_sensor", interval=0.01, group="g")
        async def a_sensor() -> dict[str, object]:
            raise RuntimeError("boom")

        @app.telemetry(name="b_sensor", interval=0.01, group="g")
        async def b_sensor() -> dict[str, object]:
            b_called.set()
            return {"value": 42}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await b_called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert b_called.is_set()
        # b_sensor published despite a_sensor crashing
        msgs = mock_mqtt.get_messages_for("testapp/b_sensor/state")
        assert len(msgs) >= 1

    async def test_handler_returning_none_does_not_affect_group(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler returning None doesn't prevent next handler from executing."""
        app = App(name="testapp", version="1.0.0")
        b_called = asyncio.Event()

        @app.telemetry(name="null_sensor", interval=0.01, group="g")
        async def null_sensor() -> dict[str, object] | None:
            return None

        @app.telemetry(name="real_sensor", interval=0.01, group="g")
        async def real_sensor() -> dict[str, object]:
            b_called.set()
            return {"value": 99}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await b_called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert b_called.is_set()
        # real_sensor published despite null_sensor returning None
        msgs = mock_mqtt.get_messages_for("testapp/real_sensor/state")
        assert len(msgs) >= 1
        # null_sensor should NOT have published anything
        null_msgs = mock_mqtt.get_messages_for("testapp/null_sensor/state")
        assert len(null_msgs) == 0


# ---------------------------------------------------------------------------
# TestGroupSchedulerInit
# ---------------------------------------------------------------------------


class TestGroupSchedulerInit:
    """Init-function handling for grouped handlers.

    Technique: Error Isolation — verify that a failed init excludes
    only the affected handler, not the entire group.
    """

    async def test_init_failure_excludes_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler whose init raises is excluded; other handler continues."""
        app = App(name="testapp", version="1.0.0")
        good_called = asyncio.Event()

        def bad_init() -> _BadInit:
            raise RuntimeError("init failed")

        @app.telemetry(name="broken", interval=0.01, group="g", init=bad_init)
        async def broken(init_result: _BadInit) -> dict[str, object]:
            return {"should": "never"}

        @app.telemetry(name="healthy", interval=0.01, group="g")
        async def healthy() -> dict[str, object]:
            good_called.set()
            return {"status": "ok"}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await good_called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert good_called.is_set()
        # healthy handler published
        msgs = mock_mqtt.get_messages_for("testapp/healthy/state")
        assert len(msgs) >= 1
        # broken handler never published
        broken_msgs = mock_mqtt.get_messages_for("testapp/broken/state")
        assert len(broken_msgs) == 0


# ---------------------------------------------------------------------------
# TestGroupSchedulerPublishStrategy
# ---------------------------------------------------------------------------


class TestGroupSchedulerPublishStrategy:
    """Per-handler publish strategy state within a group.

    Technique: Integration Testing — verify that each handler in a
    group maintains independent publish-strategy state.
    """

    async def test_per_handler_strategy_state(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Each handler has independent OnChange() state.

        Handler A returns the same value every call → publishes once.
        Handler B returns different values each call → publishes each time.
        """
        app = App(name="testapp", version="1.0.0")
        a_count = 0
        b_count = 0
        enough = asyncio.Event()

        @app.telemetry(name="stable", interval=0.01, group="g", publish=OnChange())
        async def stable() -> dict[str, object]:
            nonlocal a_count
            a_count += 1
            if a_count >= 3:
                enough.set()
            return {"value": 100}  # always the same

        @app.telemetry(name="changing", interval=0.01, group="g", publish=OnChange())
        async def changing() -> dict[str, object]:
            nonlocal b_count
            b_count += 1
            return {"value": b_count}  # different each time

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # stable: only first publish goes through (duplicates suppressed)
        stable_msgs = mock_mqtt.get_messages_for("testapp/stable/state")
        assert len(stable_msgs) == 1

        # changing: each call publishes (values differ)
        changing_msgs = mock_mqtt.get_messages_for("testapp/changing/state")
        assert len(changing_msgs) >= 3


# ---------------------------------------------------------------------------
# TestGroupSchedulerEdgeCases
# ---------------------------------------------------------------------------


class TestGroupSchedulerEdgeCases:
    """Edge-case scenarios for the coalescing group scheduler.

    Technique: Boundary Value Analysis — exercise non-obvious timing
    boundaries, total init failure, shutdown-during-sleep, and
    multi-group independence.
    """

    async def test_float_precision_integer_ms_coalescing(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Integer-ms arithmetic ensures 0.3 s and 0.6 s coalesce exactly.

        At t=0 both fire.  At t=0.3 only "fast" fires.  At t=0.6 both
        fire again because 600 ms is exactly divisible by 300 ms — no
        floating-point drift.

        Technique: Boundary Value Analysis — verifies that the
        integer-millisecond tick conversion (``_to_ms``) avoids
        accumulation errors for intervals that would drift under
        naive float arithmetic (e.g. 0.1+0.1+0.1 != 0.3).
        """
        app = App(name="testapp", version="1.0.0")
        call_log: list[tuple[str, int]] = []
        tick = 0
        enough = asyncio.Event()

        @app.telemetry(name="fast", interval=0.3, group="g")
        async def fast() -> dict[str, object]:
            nonlocal tick
            call_log.append(("fast", tick))
            tick += 1
            return {"f": tick}

        @app.telemetry(name="slow", interval=0.6, group="g")
        async def slow() -> dict[str, object]:
            call_log.append(("slow", tick))
            # After the third batch we have enough data
            if tick >= 3:
                enough.set()
            return {"s": tick}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=10.0,
        )

        # Extract handler names per batch (grouped by sequential tick counter)
        # t=0 batch: fast, slow (both fire at 0 ms)
        # t=0.3 batch: fast alone (300 ms, slow not due until 600 ms)
        # t=0.6 batch: fast, slow (both fire at 600 ms exactly)
        names = [name for name, _ in call_log]
        assert names[0] == "fast"
        assert names[1] == "slow"
        # After t=0 batch, fast fires alone at t=0.3
        assert names[2] == "fast"
        # At t=0.6 both coincide again
        assert names[3] == "fast"
        assert names[4] == "slow"

    async def test_all_handlers_fail_init(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Group where every handler has a failing init returns cleanly.

        Technique: Error Guessing — anticipating the edge case where
        the heap is empty after init and the scheduler must exit
        without entering the main loop.
        """
        app = App(name="testapp", version="1.0.0")

        def bad_init_a() -> _BadInit:
            raise RuntimeError("init A failed")

        def bad_init_b() -> _BadInit:
            raise RuntimeError("init B failed")

        @app.telemetry(name="broken_a", interval=0.01, group="g", init=bad_init_a)
        async def broken_a(init_result: _BadInit) -> dict[str, object]:
            return {"should": "never"}

        @app.telemetry(name="broken_b", interval=0.01, group="g", init=bad_init_b)
        async def broken_b(init_result: _BadInit) -> dict[str, object]:
            return {"should": "never"}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            # Give the group scheduler time to start and discover all inits failed
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Neither handler should have published anything
        assert len(mock_mqtt.get_messages_for("testapp/broken_a/state")) == 0
        assert len(mock_mqtt.get_messages_for("testapp/broken_b/state")) == 0

    async def test_shutdown_during_sleep_exits_cleanly(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Shutdown requested while the scheduler is sleeping exits cleanly.

        Technique: State Transition Testing — verify the transition
        from sleeping to shutdown without errors or hangs.
        """
        app = App(name="testapp", version="1.0.0")
        first_call = asyncio.Event()

        @app.telemetry(name="sensor", interval=10.0, group="g")
        async def sensor() -> dict[str, object]:
            first_call.set()
            return {"v": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            # Wait for the first fire at t=0, then shut down immediately
            # while the scheduler is sleeping toward t=10s
            await first_call.wait()
            await asyncio.sleep(0.02)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        # Must complete without hanging or raising
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert first_call.is_set()
        msgs = mock_mqtt.get_messages_for("testapp/sensor/state")
        assert len(msgs) >= 1

    async def test_multiple_groups_run_independently(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Two separate groups run independently without interference.

        Technique: Integration Testing — verify that separate group
        schedulers operate concurrently and produce correct output
        for their respective handlers.
        """
        app = App(name="testapp", version="1.0.0")
        group_a_called = asyncio.Event()
        group_b_called = asyncio.Event()

        @app.telemetry(name="sensor_a1", interval=0.01, group="bus_a")
        async def sensor_a1() -> dict[str, object]:
            group_a_called.set()
            return {"a1": 1}

        @app.telemetry(name="sensor_a2", interval=0.01, group="bus_a")
        async def sensor_a2() -> dict[str, object]:
            return {"a2": 2}

        @app.telemetry(name="sensor_b1", interval=0.01, group="bus_b")
        async def sensor_b1() -> dict[str, object]:
            group_b_called.set()
            return {"b1": 3}

        @app.telemetry(name="sensor_b2", interval=0.01, group="bus_b")
        async def sensor_b2() -> dict[str, object]:
            return {"b2": 4}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await group_a_called.wait()
            await group_b_called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert group_a_called.is_set()
        assert group_b_called.is_set()
        # All four handlers published
        assert len(mock_mqtt.get_messages_for("testapp/sensor_a1/state")) >= 1
        assert len(mock_mqtt.get_messages_for("testapp/sensor_a2/state")) >= 1
        assert len(mock_mqtt.get_messages_for("testapp/sensor_b1/state")) >= 1
        assert len(mock_mqtt.get_messages_for("testapp/sensor_b2/state")) >= 1


# ---------------------------------------------------------------------------
# TestToMsHelper
# ---------------------------------------------------------------------------


class TestToMsHelper:
    """Unit tests for _to_ms integer-millisecond conversion helper.

    Technique: Boundary Value Analysis — verify clamping at the sub-ms
    boundary and correct conversion of typical values.
    """

    def test_typical_conversion(self) -> None:
        """Standard intervals convert correctly."""
        from cosalette._app import _to_ms

        assert _to_ms(1.0) == 1000
        assert _to_ms(0.3) == 300
        assert _to_ms(0.001) == 1

    def test_sub_ms_clamped_to_one(self) -> None:
        """Intervals below 0.5ms round to 0 but are clamped to 1ms.

        Without the clamp, the scheduler would reschedule at the same
        fire_time (0ms offset), causing an infinite busy-loop.
        """
        from cosalette._app import _to_ms

        assert _to_ms(0.0004) == 1
        assert _to_ms(0.0001) == 1

    def test_zero_and_negative_return_zero(self) -> None:
        """Zero and negative inputs map to 0ms (degenerate, pre-validated)."""
        from cosalette._app import _to_ms

        assert _to_ms(0) == 0
        assert _to_ms(-1.0) == 0
