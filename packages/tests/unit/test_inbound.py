"""Tests for ``@app.inbound()`` — subscribing to external MQTT topics.

Test Techniques Used:
    - State-based Testing: registration state on the router and on ``App``.
    - Behavioural Testing: routing dispatches to the correct handler.
    - Specification-based Testing: topic validation, AsyncAPI channel shape.
    - Equivalence Partitioning: valid vs. wildcard vs. empty topics.
    - Decision Table Testing: inbound vs. prefix-pattern routing priority.
    - Boundary Value Analysis: single vs. multi (name_spec) registrations.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from cosalette._app import App
from cosalette._app._inbound import _validate_inbound_topic
from cosalette._mqtt._router import TopicRouter
from cosalette._registration import _InboundRegistration
from cosalette._schema import ChannelSchema, _device_name_from_archetype
from cosalette._schema._acl import derive_acl_principals
from cosalette._schema._consumer_gen import _is_consumer_visible
from cosalette._schema._loader import load_schema
from cosalette._schema._loader_helpers import _VALID_ARCHETYPES
from cosalette._settings import Settings
from cosalette._wiring._resolution import resolve_enabled
from cosalette._wiring._resolution_checks import (
    _check_expanded_duplicates,
    _expand_inbound_names,
)
from cosalette.testing import AppHarness

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_handler(topic: str, payload: str) -> None:
    """No-op async handler for router-level registration tests."""


async def _dummy_func(payload: str) -> None:
    """No-op async handler for _InboundRegistration fixtures."""


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
# TestTopicRouterInbound
# ---------------------------------------------------------------------------


class TestTopicRouterInbound:
    """Router-level registration, routing, and subscription behaviour."""

    async def test_register_and_route(self, router: TopicRouter) -> None:
        """A registered inbound handler receives routed messages."""
        received: list[tuple[str, str]] = []

        async def handler(topic: str, payload: str) -> None:
            received.append((topic, payload))

        router.register_inbound("openhab/relay/state", handler)
        await router.route("openhab/relay/state", "ON")
        await router.wait_idle()

        assert received == [("openhab/relay/state", "ON")]

    async def test_subscriptions_include_inbound(self, router: TopicRouter) -> None:
        """The subscriptions list includes registered inbound topics."""
        router.register_inbound("openhab/relay/state", _noop_handler)

        assert "openhab/relay/state" in router.subscriptions

    async def test_inbound_topics_property(self, router: TopicRouter) -> None:
        """inbound_topics returns a sorted list of registered topics."""
        router.register_inbound("openhab/relay/state", _noop_handler)
        router.register_inbound("openhab/blind/state", _noop_handler)

        assert router.inbound_topics == [
            "openhab/blind/state",
            "openhab/relay/state",
        ]

    async def test_duplicate_topic_raises(self, router: TopicRouter) -> None:
        """Registering the same inbound topic twice raises ValueError."""
        router.register_inbound("openhab/relay/state", _noop_handler)

        with pytest.raises(ValueError, match="already registered"):
            router.register_inbound("openhab/relay/state", _noop_handler)

    async def test_route_priority(self, router: TopicRouter) -> None:
        """A topic matching both an inbound handler and the device prefix
        pattern is dispatched to the inbound handler.
        """
        device_calls: list[str] = []
        inbound_calls: list[str] = []

        async def device_handler(topic: str, payload: str) -> None:
            device_calls.append(payload)

        async def inbound_handler(topic: str, payload: str) -> None:
            inbound_calls.append(payload)

        router.register("blind", device_handler)
        router.register_inbound("myapp/blind/set", inbound_handler)

        await router.route("myapp/blind/set", "payload")
        await router.wait_idle()

        assert inbound_calls == ["payload"]
        assert device_calls == []

    async def test_backpressure_config(self, router: TopicRouter) -> None:
        """Custom maxsize/backpressure are stored on the inbound entity."""
        router.register_inbound(
            "openhab/relay/state",
            _noop_handler,
            maxsize=5,
            backpressure="drop_oldest",
        )

        entity = router._inbound_handlers["openhab/relay/state"]
        assert entity.maxsize == 5
        assert entity.backpressure == "drop_oldest"
        assert entity.queue.maxsize == 5

    async def test_device_and_inbound_worker_keys_do_not_collide(
        self, router: TopicRouter
    ) -> None:
        """A device name matching an inbound entity label drains both queues.

        Technique: Error Guessing - protects the former string worker-key collision.
        """
        received: list[str] = []

        async def device_handler(topic: str, payload: str) -> None:
            received.append(f"device:{payload}")

        async def inbound_handler(topic: str, payload: str) -> None:
            received.append(f"inbound:{payload}")

        router.register("inbound:external/topic", device_handler)
        router.register_inbound("external/topic", inbound_handler)

        await router.route("myapp/inbound:external/topic/set", "device")
        await router.route("external/topic", "inbound")
        await router.wait_idle()

        assert sorted(received) == ["device:device", "inbound:inbound"]


# ---------------------------------------------------------------------------
# TestTopicValidation
# ---------------------------------------------------------------------------


class TestTopicValidation:
    """``_validate_inbound_topic`` rejects empty and wildcard topics."""

    def test_empty_topic_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_inbound_topic("")

    @pytest.mark.parametrize("topic", ["openhab/+/state", "+"])
    def test_wildcard_plus_raises(self, topic: str) -> None:
        with pytest.raises(ValueError, match="wildcards"):
            _validate_inbound_topic(topic)

    @pytest.mark.parametrize("topic", ["openhab/#", "#"])
    def test_wildcard_hash_raises(self, topic: str) -> None:
        with pytest.raises(ValueError, match="wildcards"):
            _validate_inbound_topic(topic)

    def test_valid_topic_passes(self) -> None:
        _validate_inbound_topic("openhab/relay/state")


# ---------------------------------------------------------------------------
# TestAsyncApiInbound
# ---------------------------------------------------------------------------


class TestAsyncApiInbound:
    """AsyncAPI schema generation for inbound registrations."""

    @pytest.fixture
    def inbound_app(self) -> App:
        app = App(name="bridge", version="0.5.0")

        @app.inbound("relay", topic="openhab/relay/state")
        async def _handler(payload: str) -> None:
            pass

        return app

    def test_inbound_channel_in_schema(self, inbound_app: App) -> None:
        doc = inbound_app.asyncapi()
        channel = doc["channels"]["inbound_relay"]

        assert channel["x-cosalette-archetype"] == "inbound"
        assert channel["address"] == "openhab/relay/state"

        ops = doc["operations"]
        assert "receiveRelayInbound" in ops
        assert ops["receiveRelayInbound"]["action"] == "receive"
        assert ops["receiveRelayInbound"]["channel"] == {
            "$ref": "#/channels/inbound_relay"
        }

    async def test_inbound_acl_subscribe(self, inbound_app: App) -> None:
        registry = await load_schema(inbound_app.asyncapi())

        principals = derive_acl_principals(registry)

        app_principal = next(p for p in principals if p.name == "bridge")
        assert "openhab/relay/state" in app_principal.subscribe_topics
        assert "openhab/relay/state" not in app_principal.publish_topics
        assert "openhab/relay/state" not in app_principal.publish_topics


# ---------------------------------------------------------------------------
# TestConsumerVisibility
# ---------------------------------------------------------------------------


class TestConsumerVisibility:
    """Inbound channels are excluded from HA/openHAB discovery generation."""

    def test_inbound_not_consumer_visible(self) -> None:
        channel = ChannelSchema(
            address="openhab/relay/state",
            address_template="openhab/relay/state",
            direction="receive",
            app_name="bridge",
            archetype="inbound",
        )

        assert _is_consumer_visible(channel) is False


# ---------------------------------------------------------------------------
# TestSchemaLoading
# ---------------------------------------------------------------------------


class TestSchemaLoading:
    """Loader-level recognition of the ``inbound`` archetype."""

    def test_inbound_valid_archetype(self) -> None:
        assert "inbound" in _VALID_ARCHETYPES

    def test_inbound_no_device_name(self) -> None:
        channel = ChannelSchema(
            address="openhab/relay/state",
            address_template="openhab/relay/state",
            direction="receive",
            app_name="bridge",
            archetype="inbound",
        )

        assert _device_name_from_archetype(channel) is None


# ---------------------------------------------------------------------------
# TestInboundExpansion
# ---------------------------------------------------------------------------


class TestInboundExpansion:
    """``_expand_inbound_names`` — callable name_spec expansion."""

    def test_expand_single_inbound(self) -> None:
        reg = _InboundRegistration(
            name="relay",
            func=_dummy_func,
            injection_plan=[],
            topic="openhab/relay/state",
            name_spec=None,
        )
        inbounds = [reg]

        _expand_inbound_names(inbounds, Settings())

        assert inbounds == [reg]

    def test_expand_multi_inbound(self) -> None:
        def name_spec(settings: Settings) -> dict[str, str]:
            return {
                "relay1": "openhab/relay1/state",
                "relay2": "openhab/relay2/state",
            }

        reg = _InboundRegistration(
            name="placeholder",
            func=_dummy_func,
            injection_plan=[],
            topic=None,
            topic_spec=lambda config: config,
            name_spec=name_spec,
        )
        inbounds = [reg]

        _expand_inbound_names(inbounds, Settings())

        assert len(inbounds) == 2
        topics_by_name = {r.name: r.topic for r in inbounds}
        assert topics_by_name == {
            "relay1": "openhab/relay1/state",
            "relay2": "openhab/relay2/state",
        }
        assert all(r.name_spec is None for r in inbounds)
        assert all(r.topic_spec is None for r in inbounds)


# ---------------------------------------------------------------------------
# TestInboundDecorator
# ---------------------------------------------------------------------------


class TestInboundDecorator:
    """``@app.inbound`` decorator registration behaviour."""

    def test_decorator_registers_inbound(self, app: App) -> None:
        @app.inbound(topic="foo/bar")
        async def handler(payload: str) -> None:
            pass

        assert len(app._inbounds) == 1
        reg = app._inbounds[0]
        assert isinstance(reg, _InboundRegistration)
        assert reg.topic == "foo/bar"

    def test_decorator_callable_topic(self, app: App) -> None:
        def topic_spec(config: str) -> str:
            return config

        @app.inbound("relay", topic=topic_spec)
        async def handler(payload: str) -> None:
            pass

        reg = app._inbounds[0]
        assert reg.topic is None
        assert reg.topic_spec is topic_spec

    @pytest.mark.parametrize("topic", ["", "openhab/+/state", "openhab/#"])
    def test_deferred_inbound_rejects_invalid_literal_topic(
        self, app: App, topic: str
    ) -> None:
        """Deferred enabled registrations validate literal topics immediately.

        Technique: Equivalence Partitioning - empty and wildcard topics are invalid.
        """
        with pytest.raises(ValueError):

            @app.inbound(topic=topic, enabled=lambda _settings: True)
            async def handler(payload: str) -> None:
                pass

    async def test_inbound_proxy_binds_raw_topic_and_payload(self) -> None:
        """Raw MQTT parameters bypass dependency injection and reach the handler.

        Technique: Specification-based Testing - inbound matches command bindings.
        """
        from cosalette._wiring._context import _register_inbound_proxy

        received: list[tuple[str, str]] = []

        async def handler(topic: str, payload: str) -> None:
            received.append((topic, payload))

        reg = _InboundRegistration(
            name="external",
            func=handler,
            injection_plan=[],
            mqtt_params=frozenset({"topic", "payload"}),
            topic="external/topic",
        )
        router = TopicRouter(topic_prefix="myapp")
        try:
            _register_inbound_proxy(reg, router, {})
            await router.route("external/topic", "value")
            await router.wait_idle()
        finally:
            await router.aclose()

        assert received == [("external/topic", "value")]

    def test_inbound_name_does_not_block_device_registration(self, app: App) -> None:
        """Inbound names do not participate in device schema identity.

        Technique: Error Guessing - a real device must not be masked by inbound
        metadata.
        """

        @app.inbound("sensor", topic="external/sensor")
        async def inbound(payload: str) -> None:
            pass

        @app.device("sensor")
        async def sensor() -> None:
            pass

        assert app.registered_names == frozenset({"sensor"})

    def test_duplicate_concrete_inbound_name_raises(self, app: App) -> None:
        """Concrete inbound names are unique at registration time.

        Technique: Equivalence Partitioning - a second identical name is invalid.
        """

        @app.inbound("external", topic="one/topic")
        async def first(payload: str) -> None:
            pass

        with pytest.raises(ValueError, match="Inbound name"):

            @app.inbound("external", topic="two/topic")
            async def second(payload: str) -> None:
                pass

    def test_duplicate_concrete_inbound_topic_raises(self, app: App) -> None:
        """Concrete inbound topics are unique at registration time.

        Technique: Equivalence Partitioning - a second identical topic is invalid.
        """

        @app.inbound("first", topic="external/topic")
        async def first(payload: str) -> None:
            pass

        with pytest.raises(ValueError, match="Inbound topic"):

            @app.inbound("second", topic="external/topic")
            async def second(payload: str) -> None:
                pass


class TestInboundBootstrap:
    """Inbound bootstrap resolution and duplicate validation.

    Test Techniques Used:
        - Decision Table Testing: callable enabled true/false registrations.
        - Boundary Value Analysis: singleton and expanded callable topic specs.
        - Error Guessing: expansion-induced name/topic collisions.
    """

    def test_expansion_resolves_singleton_callable_topic_with_settings(self) -> None:
        """A literal name resolves its topic callable during bootstrap expansion."""
        reg = _InboundRegistration(
            name="relay",
            func=_dummy_func,
            injection_plan=[],
            topic_spec=lambda _settings: "external/relay/state",
        )
        inbounds = [reg]

        _expand_inbound_names(inbounds, Settings())

        assert inbounds[0].topic == "external/relay/state"
        assert inbounds[0].topic_spec is None

    def test_resolve_enabled_prunes_disabled_inbound_before_schema_emission(
        self,
    ) -> None:
        """Only enabled inbound entries remain visible after bootstrap resolution."""
        enabled = _InboundRegistration(
            name="enabled",
            func=_dummy_func,
            injection_plan=[],
            topic="external/enabled",
            enabled_spec=lambda _settings: True,
        )
        disabled = _InboundRegistration(
            name="disabled",
            func=_dummy_func,
            injection_plan=[],
            topic="external/disabled",
            enabled_spec=lambda _settings: False,
        )
        inbounds = [enabled, disabled]

        resolve_enabled([], [], [], Settings(), None, inbound_list=inbounds)

        assert [reg.name for reg in inbounds] == ["enabled"]
        assert inbounds[0].enabled_spec is True

    @pytest.mark.parametrize("duplicate", ["name", "topic"])
    def test_expanded_inbound_duplicates_raise(self, duplicate: str) -> None:
        """Expanded inbound registrations reject duplicate names and topics."""
        first = _InboundRegistration(
            name="first",
            func=_dummy_func,
            injection_plan=[],
            topic="external/one",
        )
        second = _InboundRegistration(
            name="first" if duplicate == "name" else "second",
            func=_dummy_func,
            injection_plan=[],
            topic="external/one" if duplicate == "topic" else "external/two",
        )

        with pytest.raises(ValueError, match="Inbound"):
            _check_expanded_duplicates([], [], [], inbound_list=[first, second])

    async def test_bootstrap_wires_only_enabled_inbound_and_schema(self) -> None:
        """Enabled inbound entries subscribe and remain in the generated schema.

        Technique: Decision Table Testing - true is wired, false is removed.
        """
        harness = AppHarness.create()
        received: list[tuple[Settings, str, str]] = []
        message_received = asyncio.Event()

        @harness.app.inbound(
            "enabled", topic="external/enabled", enabled=lambda _settings: True
        )
        async def enabled(settings: Settings, topic: str, payload: str) -> None:
            received.append((settings, topic, payload))
            message_received.set()

        @harness.app.inbound(
            "disabled", topic="external/disabled", enabled=lambda _settings: False
        )
        async def disabled(payload: str) -> None:
            raise AssertionError("disabled inbound must not be wired")

        run_task = asyncio.create_task(harness.run())
        await asyncio.sleep(0)
        await harness.mqtt.deliver("external/enabled", "value")
        await asyncio.wait_for(message_received.wait(), timeout=1)
        harness.trigger_shutdown()
        await run_task

        assert [reg.name for reg in harness.app.inbound_registrations] == ["enabled"]
        assert received == [(harness.settings, "external/enabled", "value")]
        schema = harness.app.asyncapi()
        assert set(schema["channels"]) == {"inbound_enabled"}
        registry = await load_schema(schema)
        principal = next(
            p for p in derive_acl_principals(registry) if p.name == "testapp"
        )
        assert "external/enabled" in principal.subscribe_topics
        assert "external/disabled" not in principal.subscribe_topics
