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
