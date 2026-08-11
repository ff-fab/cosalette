"""Tests for cosalette._mqtt._router — MQTT command topic routing.

Test Techniques Used:
    - Specification-based Testing: topic parsing edge cases
    - State-based Testing: handler registration and duplicate rejection
    - Behavioural Testing: route dispatches to correct handler, concurrency
    - Log Assertion: WARNING for unregistered device via caplog
    - Concurrency Testing: cross-entity isolation, FIFO ordering within entity
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest

from cosalette._mqtt._router import TopicRouter

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_handler(topic: str, payload: str) -> None:
    """No-op async handler for registration tests."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def router() -> AsyncIterator[TopicRouter]:
    """TopicRouter with 'myapp' prefix; cancels workers on teardown."""
    r = TopicRouter(topic_prefix="myapp")
    yield r
    await r.aclose()


# ---------------------------------------------------------------------------
# TestExtractDevice
# ---------------------------------------------------------------------------


class TestExtractDevice:
    """Topic parsing edge cases.

    Technique: Specification-based Testing — verifying _extract_device
    returns the correct device name or None for various topic shapes.
    """

    async def test_valid_command_topic(self, router: TopicRouter) -> None:
        """Standard command topic extracts the device name."""
        assert router._extract_device("myapp/blind/set") == ("blind", None)

    async def test_non_set_suffix_ignored(self, router: TopicRouter) -> None:
        """State topics (non-/set suffix) are not command topics."""
        assert router._extract_device("myapp/blind/state") is None

    async def test_missing_prefix(self, router: TopicRouter) -> None:
        """Topic with a different prefix returns None."""
        assert router._extract_device("other/blind/set") is None

    async def test_single_sub_topic(self, router: TopicRouter) -> None:
        """One extra segment is a sub-topic, not a nested device."""
        assert router._extract_device("myapp/blind/calibrate/set") == (
            "blind",
            "calibrate",
        )

    async def test_too_many_segments(self, router: TopicRouter) -> None:
        """More than one extra segment is rejected."""
        assert router._extract_device("myapp/blind/a/b/set") is None

    async def test_empty_device_name(self, router: TopicRouter) -> None:
        """Empty device segment (double slash) returns None."""
        assert router._extract_device("myapp//set") is None

    async def test_empty_sub_topic_segment(self, router: TopicRouter) -> None:
        """Empty sub-topic segment (trailing double slash) returns None."""
        assert router._extract_device("myapp/blind//set") is None

    async def test_prefix_only(self, router: TopicRouter) -> None:
        """Topic that is just 'prefix/set' has no middle segment → None."""
        assert router._extract_device("myapp/set") is None

    async def test_exact_prefix_match(self, router: TopicRouter) -> None:
        """Prefix must match exactly, not as a substring."""
        assert router._extract_device("myapp2/blind/set") is None


# ---------------------------------------------------------------------------
# TestRegister
# ---------------------------------------------------------------------------


class TestRegister:
    """Handler registration and duplicate rejection.

    Technique: State-based Testing — verifying internal handler dict
    is populated and ValueError raised on duplicates.
    """

    async def test_register_handler(self, router: TopicRouter) -> None:
        """Registering a handler succeeds and is retrievable."""
        router.register("blind", _noop_handler)
        assert "blind" in router._handlers

    async def test_duplicate_raises_value_error(self, router: TopicRouter) -> None:
        """Registering a second handler for the same device raises ValueError."""
        router.register("blind", _noop_handler)
        with pytest.raises(ValueError, match="already registered"):
            router.register("blind", _noop_handler)


# ---------------------------------------------------------------------------
# TestRoute
# ---------------------------------------------------------------------------


class TestRoute:
    """Dispatch to correct handler and edge-case routing behaviour.

    Technique: Behavioural Testing — verifying route() calls the
    correct handler and handles missing/non-command topics gracefully.
    """

    async def test_routes_to_correct_handler(self, router: TopicRouter) -> None:
        """Command topic dispatches payload to the registered handler."""
        received: list[tuple[str, str]] = []

        async def handler(topic: str, payload: str) -> None:
            received.append((topic, payload))

        router.register("blind", handler)
        await router.route("myapp/blind/set", '{"position": 50}')
        await router.wait_idle()

        assert received == [("myapp/blind/set", '{"position": 50}')]

    async def test_unknown_device_logs_warning(
        self,
        router: TopicRouter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Valid command topic for an unregistered device logs WARNING."""
        with caplog.at_level(logging.WARNING, logger="cosalette._mqtt._router"):
            await router.route("myapp/unknown/set", "{}")

        assert "No handler registered" in caplog.text
        assert "unknown" in caplog.text

    async def test_non_command_topic_silently_ignored(
        self, router: TopicRouter
    ) -> None:
        """Non-command topic (e.g., /state) is silently ignored — no error."""
        # Should not raise or log anything
        await router.route("myapp/blind/state", "{}")

    async def test_routes_to_correct_handler_multiple_devices(
        self, router: TopicRouter
    ) -> None:
        """With multiple registered devices, each gets its own messages."""
        blind_msgs: list[tuple[str, str]] = []
        light_msgs: list[tuple[str, str]] = []

        async def blind_handler(topic: str, payload: str) -> None:
            blind_msgs.append((topic, payload))

        async def light_handler(topic: str, payload: str) -> None:
            light_msgs.append((topic, payload))

        router.register("blind", blind_handler)
        router.register("light", light_handler)

        await router.route("myapp/blind/set", "b_payload")
        await router.route("myapp/light/set", "l_payload")
        await router.wait_idle()

        assert blind_msgs == [("myapp/blind/set", "b_payload")]
        assert light_msgs == [("myapp/light/set", "l_payload")]

    async def test_handler_receives_correct_arguments(
        self, router: TopicRouter
    ) -> None:
        """Handler is called with the original (topic, payload) tuple."""
        received: list[tuple[str, str]] = []

        async def handler(topic: str, payload: str) -> None:
            received.append((topic, payload))

        router.register("sensor", handler)
        await router.route("myapp/sensor/set", "data123")
        await router.wait_idle()

        assert len(received) == 1
        assert received[0] == ("myapp/sensor/set", "data123")

    async def test_sub_topic_routes_to_device_handler(
        self, router: TopicRouter
    ) -> None:
        """Sub-topic command routes to the same device handler."""
        received: list[tuple[str, str]] = []

        async def handler(topic: str, payload: str) -> None:
            received.append((topic, payload))

        router.register("blind", handler)
        await router.route("myapp/blind/calibrate/set", "CAL")
        await router.wait_idle()

        assert received == [("myapp/blind/calibrate/set", "CAL")]


# ---------------------------------------------------------------------------
# TestSubscriptions
# ---------------------------------------------------------------------------


class TestSubscriptions:
    """Subscription list generation.

    Technique: Specification-based Testing — verifying the subscriptions
    property returns correctly formatted topic strings.
    """

    async def test_empty_router_returns_empty_list(self, router: TopicRouter) -> None:
        """Router with no registered devices returns an empty subscription list."""
        assert router.subscriptions == []

    async def test_returns_subscription_topics(self, router: TopicRouter) -> None:
        """Each registered device produces root and wildcard subscriptions."""
        router.register("blind", _noop_handler)
        router.register("light", _noop_handler)

        subs = router.subscriptions
        assert "myapp/blind/set" in subs
        assert "myapp/blind/+/set" in subs
        assert "myapp/light/set" in subs
        assert "myapp/light/+/set" in subs
        assert len(subs) == 4

    async def test_subscriptions_include_wildcard(self, router: TopicRouter) -> None:
        """Wildcard sub-topic subscription is generated for non-root devices."""
        router.register("blind", _noop_handler)

        subs = router.subscriptions
        assert "myapp/blind/+/set" in subs


# ---------------------------------------------------------------------------
# TestRootDevice — root-level device routing
# ---------------------------------------------------------------------------


class TestRootDevice:
    """Tests for root-level device routing.

    Root devices register a handler for ``{prefix}/set`` instead of
    ``{prefix}/{device}/set``.

    Technique: Behavioural + State-based Testing — verifying
    registration, dispatch, subscription, and coexistence with
    named devices.
    """

    async def test_register_root_handler(self) -> None:
        """Registering a root handler stores it on the router."""
        router = TopicRouter(topic_prefix="myapp")
        router.register("sensor", _noop_handler, is_root=True)
        assert router._root_handler is _noop_handler

    async def test_route_root_topic(self) -> None:
        """Root topic {prefix}/set dispatches to root handler."""
        router = TopicRouter(topic_prefix="myapp")
        calls: list[tuple[str, str]] = []

        async def handler(topic: str, payload: str) -> None:
            calls.append((topic, payload))

        router.register("sensor", handler, is_root=True)
        await router.route("myapp/set", "open")
        await router.wait_idle()
        assert calls == [("myapp/set", "open")]
        await router.aclose()

    async def test_root_subscription(self) -> None:
        """Root handler produces a {prefix}/set subscription."""
        router = TopicRouter(topic_prefix="myapp")
        router.register("sensor", _noop_handler, is_root=True)
        assert "myapp/set" in router.subscriptions

    async def test_duplicate_root_raises(self) -> None:
        """Registering a second root handler raises ValueError."""
        router = TopicRouter(topic_prefix="myapp")
        router.register("a", _noop_handler, is_root=True)
        with pytest.raises(ValueError, match="Root handler already registered"):
            router.register("b", _noop_handler, is_root=True)

    async def test_root_and_named_coexist(self) -> None:
        """Root and named handlers receive their own messages."""
        router = TopicRouter(topic_prefix="myapp")
        root_calls: list[str] = []
        named_calls: list[str] = []

        async def root_handler(topic: str, payload: str) -> None:
            root_calls.append(payload)

        async def named_handler(topic: str, payload: str) -> None:
            named_calls.append(payload)

        router.register("root_fn", root_handler, is_root=True)
        router.register("light", named_handler)

        await router.route("myapp/set", "root_msg")
        await router.route("myapp/light/set", "named_msg")
        await router.wait_idle()

        assert root_calls == ["root_msg"]
        assert named_calls == ["named_msg"]
        await router.aclose()

    async def test_root_topic_no_handler_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Root topic with no root handler logs WARNING."""
        router = TopicRouter(topic_prefix="myapp")
        with caplog.at_level(logging.WARNING, logger="cosalette._mqtt._router"):
            await router.route("myapp/set", "{}")
        assert "No root handler registered" in caplog.text


# ---------------------------------------------------------------------------
# TestSlashComposedNames — Router prefix composition (cos-089)
# ---------------------------------------------------------------------------


class TestSlashComposedNames:
    """TopicRouter correctly handles slash-composed device names.

    When cosalette.Router is included with a prefix, the resulting
    command registration name contains a slash
    (e.g. ``sensors/temperature``). The TopicRouter must route commands
    for these composed names correctly.

    Technique: Specification-based — verifying each combination of
    simple, compound, and sub-topic routing.
    """

    @pytest.fixture
    def composed_router(self) -> TopicRouter:
        """TopicRouter with a slash-composed handler registered."""
        r = TopicRouter(topic_prefix="myapp")
        r.register("sensors/temperature", _noop_handler)
        return r

    async def test_extract_compound_device_exact_match(
        self, composed_router: TopicRouter
    ) -> None:
        """Exact compound device name is extracted from command topic."""
        assert composed_router._extract_device("myapp/sensors/temperature/set") == (
            "sensors/temperature",
            None,
        )

    async def test_extract_compound_device_sub_topic(
        self, composed_router: TopicRouter
    ) -> None:
        """Compound device name with a trailing sub-topic segment is extracted."""
        assert composed_router._extract_device(
            "myapp/sensors/temperature/calibrate/set"
        ) == ("sensors/temperature", "calibrate")

    async def test_compound_device_too_many_segments_returns_none(
        self, composed_router: TopicRouter
    ) -> None:
        """Sub-topic with multiple slashes is rejected (not routable)."""
        assert (
            composed_router._extract_device("myapp/sensors/temperature/a/b/set") is None
        )

    async def test_route_compound_device_dispatches_correctly(self) -> None:
        """route() delivers commands to a slash-composed handler."""
        received: list[tuple[str, str]] = []

        async def handler(topic: str, payload: str) -> None:
            received.append((topic, payload))

        r = TopicRouter(topic_prefix="myapp")
        r.register("sensors/temperature", handler)

        await r.route("myapp/sensors/temperature/set", '{"value": 22}')
        await r.wait_idle()

        assert received == [("myapp/sensors/temperature/set", '{"value": 22}')]
        await r.aclose()

    async def test_compound_device_subscription_topics(self) -> None:
        """Subscriptions for a slash-composed name are well-formed MQTT topics."""
        r = TopicRouter(topic_prefix="myapp")
        r.register("sensors/temperature", _noop_handler)

        subs = r.subscriptions
        assert "myapp/sensors/temperature/set" in subs
        assert "myapp/sensors/temperature/+/set" in subs

    async def test_simple_and_compound_devices_coexist(self) -> None:
        """Simple and compound device names route independently."""
        simple_msgs: list[str] = []
        compound_msgs: list[str] = []

        async def simple_handler(topic: str, payload: str) -> None:
            simple_msgs.append(topic)

        async def compound_handler(topic: str, payload: str) -> None:
            compound_msgs.append(topic)

        r = TopicRouter(topic_prefix="myapp")
        r.register("relay", simple_handler)
        r.register("sensors/temperature", compound_handler)

        await r.route("myapp/relay/set", "{}")
        await r.route("myapp/sensors/temperature/set", "{}")
        await r.wait_idle()

        assert simple_msgs == ["myapp/relay/set"]
        assert compound_msgs == ["myapp/sensors/temperature/set"]
        await r.aclose()

    async def test_compound_topic_dispatches_to_registered_simple_name_as_subtopic(
        self,
    ) -> None:
        """Registered simple name matches compound topic via one-level sub-topic prefix.

        When ``sensors`` is registered and the topic is
        ``myapp/sensors/temperature/set``, the router extracts device name
        ``sensors`` with sub-topic ``temperature`` and dispatches to the
        registered ``sensors`` handler.  This is the primary sub-topic-prefix
        routing path — ``sensors/temperature`` need not be separately registered.
        """
        received: list[str] = []

        async def sensors_handler(topic: str, payload: str) -> None:
            received.append(topic)

        r = TopicRouter(topic_prefix="myapp")
        r.register("sensors", sensors_handler)

        # "sensors" is registered; the router matches topic prefix "sensors"
        # with sub-topic "temperature", dispatching to the sensors handler.
        await r.route("myapp/sensors/temperature/set", "{}")
        await r.wait_idle()
        assert received == ["myapp/sensors/temperature/set"]
        await r.aclose()


# ---------------------------------------------------------------------------
# TestConcurrentDispatch — per-entity worker concurrency (bug-fix: cos-igti.1)
# ---------------------------------------------------------------------------


class TestConcurrentDispatch:
    """Per-entity worker tasks run concurrently; FIFO within each entity.

    Technique: Concurrency Testing — Event-gated handlers prove that a
    slow entity does not block a fast entity (cross-entity isolation),
    and that messages to the same entity are processed in order.
    """

    async def test_cross_entity_concurrency(self) -> None:
        """Slow handler on entity A does not block entities B and C.

        Gates entity A on an asyncio.Event; routes messages to A, B, C.
        Asserts B and C complete while A is still blocked, then releases A.

        Technique: Concurrency Testing — Event gating; no real sleeps.
        """
        router = TopicRouter(topic_prefix="myapp")
        gate_a = asyncio.Event()
        a_started = asyncio.Event()
        b_done = asyncio.Event()
        c_done = asyncio.Event()
        a_done = asyncio.Event()

        async def slow_a(topic: str, payload: str) -> None:
            a_started.set()
            await gate_a.wait()
            a_done.set()

        async def fast_b(topic: str, payload: str) -> None:
            b_done.set()

        async def fast_c(topic: str, payload: str) -> None:
            c_done.set()

        router.register("a", slow_a)
        router.register("b", fast_b)
        router.register("c", fast_c)

        # Route to A first (will block at gate), then B and C
        await router.route("myapp/a/set", "msg")
        await router.route("myapp/b/set", "msg")
        await router.route("myapp/c/set", "msg")

        # B and C must complete while A is still blocked
        await asyncio.wait_for(b_done.wait(), timeout=2.0)
        await asyncio.wait_for(c_done.wait(), timeout=2.0)
        assert not a_done.is_set(), "A must still be blocked at the gate"

        # Release A and verify it completes
        gate_a.set()
        await asyncio.wait_for(a_done.wait(), timeout=2.0)

        await router.aclose()

    async def test_fifo_within_entity(self) -> None:
        """Messages to the same entity are processed in registration order.

        Routes ON then OFF to one entity; asserts the handler observed
        them strictly in that order.

        Technique: Concurrency Testing — FIFO ordering verification.
        """
        router = TopicRouter(topic_prefix="myapp")
        received: list[str] = []
        second_received = asyncio.Event()

        async def handler(topic: str, payload: str) -> None:
            received.append(payload)
            if len(received) >= 2:
                second_received.set()

        router.register("relay", handler)
        await router.route("myapp/relay/set", "ON")
        await router.route("myapp/relay/set", "OFF")
        await asyncio.wait_for(second_received.wait(), timeout=2.0)

        assert received == ["ON", "OFF"]
        await router.aclose()

    async def test_wait_idle_returns_after_handlers_complete(self) -> None:
        """wait_idle() returns only after all in-flight handlers finish.

        Technique: State-based Testing — assert side-effect only visible
        after wait_idle unblocks.
        """
        router = TopicRouter(topic_prefix="myapp")
        completed: list[str] = []

        async def handler(topic: str, payload: str) -> None:
            completed.append(payload)

        router.register("lamp", handler)
        await router.route("myapp/lamp/set", "X")
        # Immediately after route(), handler may not have run yet
        await router.wait_idle()
        assert completed == ["X"]
        await router.aclose()

    async def test_aclose_cancels_workers_cleanly(self) -> None:
        """aclose() cancels workers; subsequent wait_idle on empty queues returns.

        Technique: State-based Testing — verify no exception leaks and
        aclose is idempotent.
        """
        router = TopicRouter(topic_prefix="myapp")

        async def handler(topic: str, payload: str) -> None:
            pass

        router.register("device", handler)
        # Spin up a worker by routing a message, then drain it
        await router.route("myapp/device/set", "ping")
        await router.wait_idle()

        # aclose must not raise
        await router.aclose()

        # Second aclose is idempotent
        await router.aclose()

        # wait_idle on empty queues returns immediately
        await router.wait_idle()
