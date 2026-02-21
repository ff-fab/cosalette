"""Tests for cosalette._router — MQTT command topic routing.

Test Techniques Used:
    - Specification-based Testing: topic parsing edge cases
    - State-based Testing: handler registration and duplicate rejection
    - Behavioural Testing: route dispatches to correct handler
    - Log Assertion: WARNING for unregistered device via caplog
"""

from __future__ import annotations

import logging

import pytest

from cosalette._router import TopicRouter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_handler(topic: str, payload: str) -> None:
    """No-op async handler for registration tests."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def router() -> TopicRouter:
    """TopicRouter with 'myapp' prefix."""
    return TopicRouter(topic_prefix="myapp")


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
        assert router._extract_device("myapp/blind/set") == "blind"

    async def test_non_set_suffix_ignored(self, router: TopicRouter) -> None:
        """State topics (non-/set suffix) are not command topics."""
        assert router._extract_device("myapp/blind/state") is None

    async def test_missing_prefix(self, router: TopicRouter) -> None:
        """Topic with a different prefix returns None."""
        assert router._extract_device("other/blind/set") is None

    async def test_nested_device_path(self, router: TopicRouter) -> None:
        """Nested path (extra slash) in the device segment returns None."""
        assert router._extract_device("myapp/floor1/blind/set") is None

    async def test_empty_device_name(self, router: TopicRouter) -> None:
        """Empty device segment (double slash) returns None."""
        assert router._extract_device("myapp//set") is None

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

        assert received == [("myapp/blind/set", '{"position": 50}')]

    async def test_unknown_device_logs_warning(
        self,
        router: TopicRouter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Valid command topic for an unregistered device logs WARNING."""
        with caplog.at_level(logging.WARNING, logger="cosalette._router"):
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

        assert len(received) == 1
        assert received[0] == ("myapp/sensor/set", "data123")


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
        """Each registered device produces a '{prefix}/{device}/set' subscription."""
        router.register("blind", _noop_handler)
        router.register("light", _noop_handler)

        subs = router.subscriptions
        assert "myapp/blind/set" in subs
        assert "myapp/light/set" in subs
        assert len(subs) == 2


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
        assert calls == [("myapp/set", "open")]

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

        assert root_calls == ["root_msg"]
        assert named_calls == ["named_msg"]

    async def test_root_topic_no_handler_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Root topic with no root handler logs WARNING."""
        router = TopicRouter(topic_prefix="myapp")
        with caplog.at_level(logging.WARNING, logger="cosalette._router"):
            await router.route("myapp/set", "{}")
        assert "No root handler registered" in caplog.text
