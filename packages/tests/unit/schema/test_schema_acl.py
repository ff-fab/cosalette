"""Unit tests for cosalette._schema._acl — ACL generation from AsyncAPI schemas.

Test Techniques Used:
- Specification-based Testing: derive_acl_principals and formatter contracts
- Equivalence Partitioning: deploy/per-app/monitor principal types, broker formats
- Branch Coverage: publish vs subscribe directions, wildcard handling
- Round-trip Testing: formatter output structure verification
"""

from __future__ import annotations

import pytest

from cosalette._app import App
from cosalette._schema import (
    ChannelSchema,
    EnforcementConfig,
    OperationSchema,
    SchemaRegistry,
)
from cosalette._schema._acl import FORMATTERS, AclPrincipal, derive_acl_principals
from cosalette._schema._loader import load_schema

pytestmark = pytest.mark.unit


def _make_network_registry() -> SchemaRegistry:
    """Create a 2-app network schema for testing."""
    return SchemaRegistry(
        app_name=None,  # Network-level schema
        app_version="2.1.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="strict", network_level=True),
        channels={
            "thermo2mqtt/temperature/state": ChannelSchema(
                address="thermo2mqtt/temperature/state",
                address_template="thermo2mqtt/temperature/state",
                direction="send",
                app_name="thermo2mqtt",
                archetype="telemetry",
            ),
            "thermo2mqtt/setpoint/state": ChannelSchema(
                address="thermo2mqtt/setpoint/state",
                address_template="thermo2mqtt/setpoint/state",
                direction="send",
                app_name="thermo2mqtt",
                archetype="telemetry",
            ),
            "thermo2mqtt/{deviceName}/set": ChannelSchema(
                address="thermo2mqtt/{deviceName}/set",
                address_template="thermo2mqtt/{deviceName}/set",
                direction="receive",
                app_name="thermo2mqtt",
                archetype="command",
            ),
            "hvac2mqtt/climate/state": ChannelSchema(
                address="hvac2mqtt/climate/state",
                address_template="hvac2mqtt/climate/state",
                direction="send",
                app_name="hvac2mqtt",
                archetype="telemetry",
            ),
            "hvac2mqtt/{deviceName}/set": ChannelSchema(
                address="hvac2mqtt/{deviceName}/set",
                address_template="hvac2mqtt/{deviceName}/set",
                direction="receive",
                app_name="hvac2mqtt",
                archetype="command",
            ),
        },
        operations={
            "publishTemperature": OperationSchema(
                action="send",
                channel_ref="thermo2mqtt/temperature/state",
                archetype="telemetry",
            ),
            "publishSetpoint": OperationSchema(
                action="send",
                channel_ref="thermo2mqtt/setpoint/state",
                archetype="telemetry",
            ),
            "receiveThermCommand": OperationSchema(
                action="receive",
                channel_ref="thermo2mqtt/{deviceName}/set",
                archetype="command",
            ),
            "publishClimate": OperationSchema(
                action="send",
                channel_ref="hvac2mqtt/climate/state",
                archetype="telemetry",
            ),
            "receiveHvacCommand": OperationSchema(
                action="receive",
                channel_ref="hvac2mqtt/{deviceName}/set",
                archetype="command",
            ),
        },
        component_schemas={},
        device_names=frozenset(),
    )


def _make_single_app_registry() -> SchemaRegistry:
    """Create a single-app schema for testing."""
    return SchemaRegistry(
        app_name="thermo2mqtt",
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="warn"),
        channels={
            "thermo2mqtt/temperature/state": ChannelSchema(
                address="thermo2mqtt/temperature/state",
                address_template="thermo2mqtt/temperature/state",
                direction="send",
                app_name="thermo2mqtt",
                archetype="telemetry",
            ),
            "thermo2mqtt/{deviceName}/set": ChannelSchema(
                address="thermo2mqtt/{deviceName}/set",
                address_template="thermo2mqtt/{deviceName}/set",
                direction="receive",
                app_name="thermo2mqtt",
                archetype="command",
            ),
        },
        operations={
            "publishTemperature": OperationSchema(
                action="send",
                channel_ref="thermo2mqtt/temperature/state",
                archetype="telemetry",
            ),
            "receiveCommand": OperationSchema(
                action="receive",
                channel_ref="thermo2mqtt/{deviceName}/set",
                archetype="command",
            ),
        },
        component_schemas={},
        device_names=frozenset(),
    )


class TestDerivePrincipals:
    def test_derive_principals_from_network_schema(self) -> None:
        """Network schema with 2 apps produces deploy + 2 app principals + monitor."""
        registry = _make_network_registry()
        principals = derive_acl_principals(registry)

        assert len(principals) == 4  # deploy + thermo2mqtt + hvac2mqtt + monitor

        names = {p.name for p in principals}
        assert names == {"deploy", "thermo2mqtt", "hvac2mqtt", "monitor"}

    def test_derive_principals_single_app(self) -> None:
        """Single-app schema produces deploy + 1 app + monitor."""
        registry = _make_single_app_registry()
        principals = derive_acl_principals(registry)

        assert len(principals) == 3  # deploy + thermo2mqtt + monitor

        names = {p.name for p in principals}
        assert names == {"deploy", "thermo2mqtt", "monitor"}

    def test_derive_principals_single_app_with_prefix(self) -> None:
        """Single-app mode with app_name only creates deploy + that app + monitor."""
        registry = _make_network_registry()
        principals = derive_acl_principals(registry, app_name="thermo2mqtt")

        assert len(principals) == 3  # deploy + thermo2mqtt + monitor

        names = {p.name for p in principals}
        assert names == {"deploy", "thermo2mqtt", "monitor"}

    def test_deploy_has_wildcard_access(self) -> None:
        """deploy principal has # in both pub and sub."""
        registry = _make_single_app_registry()
        principals = derive_acl_principals(registry)

        deploy = next(p for p in principals if p.name == "deploy")
        assert deploy.publish_topics == ("#",)
        assert deploy.subscribe_topics == ("#",)

    def test_monitor_subscribe_only(self) -> None:
        """monitor has subscribe topics, no publish."""
        registry = _make_single_app_registry()
        principals = derive_acl_principals(registry)

        monitor = next(p for p in principals if p.name == "monitor")
        assert monitor.publish_topics == ()
        assert len(monitor.subscribe_topics) > 0
        assert all("+" in topic for topic in monitor.subscribe_topics)

    def test_app_principal_publish_includes_framework_topics(self) -> None:
        """status, error, schema/status, etc."""
        registry = _make_single_app_registry()
        principals = derive_acl_principals(registry)

        thermo = next(p for p in principals if p.name == "thermo2mqtt")

        # Should include framework topics
        expected_framework = {
            "thermo2mqtt/status",
            "thermo2mqtt/error",
            "thermo2mqtt/schema/status",
            "thermo2mqtt/_meta/registry",
            "thermo2mqtt/+/availability",
            "thermo2mqtt/+/error",
        }

        for topic in expected_framework:
            assert topic in thermo.publish_topics

    def test_app_principal_subscribe_includes_commands(self) -> None:
        """command channels in subscribe list."""
        registry = _make_single_app_registry()
        principals = derive_acl_principals(registry)

        thermo = next(p for p in principals if p.name == "thermo2mqtt")

        # Should subscribe to command channel with wildcard
        assert "thermo2mqtt/+/set" in thermo.subscribe_topics
        assert "cosalette/schema/update" in thermo.subscribe_topics


class TestFormatters:
    def test_format_mosquitto_output(self) -> None:
        """Snapshot test: output matches expected Mosquitto format."""
        principals = [
            AclPrincipal(
                name="deploy",
                publish_topics=("#",),
                subscribe_topics=("#",),
            ),
            AclPrincipal(
                name="thermo2mqtt",
                publish_topics=("thermo2mqtt/status", "thermo2mqtt/temperature/state"),
                subscribe_topics=("cosalette/schema/update", "thermo2mqtt/+/set"),
            ),
            AclPrincipal(
                name="monitor",
                publish_topics=(),
                subscribe_topics=("+/schema/status", "+/status"),
            ),
        ]

        output = FORMATTERS["mosquitto"](principals)

        expected_lines = [
            "# Generated by cosalette schema acl",
            "# Broker: Mosquitto",
            "",
            "user deploy",
            "topic readwrite #",
            "",
            "user thermo2mqtt",
            "topic write thermo2mqtt/status",
            "topic write thermo2mqtt/temperature/state",
            "topic read cosalette/schema/update",
            "topic read thermo2mqtt/+/set",
            "",
            "user monitor",
            "topic read +/schema/status",
            "topic read +/status",
            "",
        ]

        assert output == "\n".join(expected_lines)

    def test_format_emqx_output(self) -> None:
        """Snapshot test for EMQX."""
        principals = [
            AclPrincipal(
                name="deploy",
                publish_topics=("#",),
                subscribe_topics=("#",),
            ),
            AclPrincipal(
                name="thermo2mqtt",
                publish_topics=("thermo2mqtt/status",),
                subscribe_topics=("thermo2mqtt/+/set",),
            ),
        ]

        output = FORMATTERS["emqx"](principals)

        expected_lines = [
            "%% Generated by cosalette schema acl",
            "%% Broker: EMQX",
            "",
            '{allow, {user, "deploy"}, all, ["#"]}.',
            '{allow, {user, "thermo2mqtt"}, publish, ["thermo2mqtt/status"]}.',
            '{allow, {user, "thermo2mqtt"}, subscribe, ["thermo2mqtt/+/set"]}.',
            "",
            "{deny, all}.",
        ]

        assert output == "\n".join(expected_lines)

    def test_format_hivemq_output(self) -> None:
        """Snapshot test for HiveMQ XML."""
        principals = [
            AclPrincipal(
                name="deploy",
                publish_topics=("#",),
                subscribe_topics=("#",),
            ),
        ]

        output = FORMATTERS["hivemq"](principals)

        # Basic XML structure validation
        assert output.startswith("<?xml version='1.0' encoding='utf-8'?>")
        assert "<file-rbac>" in output
        assert "<users>" in output
        assert "<roles>" in output
        assert "<name>deploy</name>" in output
        assert "<topic>#</topic>" in output

    def test_format_nanomq_output(self) -> None:
        """Snapshot test for NanoMQ."""
        principals = [
            AclPrincipal(
                name="deploy",
                publish_topics=("#",),
                subscribe_topics=("#",),
            ),
            AclPrincipal(
                name="thermo2mqtt",
                publish_topics=("thermo2mqtt/status",),
                subscribe_topics=("thermo2mqtt/+/set",),
            ),
        ]

        output = FORMATTERS["nanomq"](principals)
        lines = output.split("\n")

        assert lines[0] == "# Generated by cosalette schema acl"
        assert lines[1] == "# Broker: NanoMQ"
        assert lines[3] == "rules = ["

        import json

        deploy_rule = json.loads(lines[4].strip().rstrip(","))
        assert deploy_rule["username"] == "deploy"
        assert deploy_rule["action"] == "pubsub"
        assert deploy_rule["topics"] == ["#"]

        pub_rule = json.loads(lines[5].strip().rstrip(","))
        assert pub_rule["username"] == "thermo2mqtt"
        assert pub_rule["action"] == "publish"
        assert pub_rule["topics"] == ["thermo2mqtt/status"]

        sub_rule = json.loads(lines[6].strip().rstrip(","))
        assert sub_rule["username"] == "thermo2mqtt"
        assert sub_rule["action"] == "subscribe"
        assert sub_rule["topics"] == ["thermo2mqtt/+/set"]

        deny_rule = json.loads(lines[7].strip())
        assert deny_rule["permit"] == "deny"

        assert lines[-1] == "]"

    def test_all_formatters_registered(self) -> None:
        """FORMATTERS dict has all 5 entries."""
        assert len(FORMATTERS) == 5
        expected_formatters = {"mosquitto", "emqx", "hivemq", "vernemq", "nanomq"}
        assert set(FORMATTERS.keys()) == expected_formatters

        # Verify all are callable
        for formatter in FORMATTERS.values():
            assert callable(formatter)


class TestAclPrincipal:
    def test_acl_principal_immutable(self) -> None:
        """AclPrincipal is frozen dataclass."""
        principal = AclPrincipal(
            name="test",
            publish_topics=("topic1",),
            subscribe_topics=("topic2",),
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            principal.name = "modified"  # ty: ignore[invalid-assignment]

        # Tuples should be immutable too
        assert isinstance(principal.publish_topics, tuple)
        assert isinstance(principal.subscribe_topics, tuple)


# ---------------------------------------------------------------------------
# cos-tc2v — _find_channel must resolve the channel *key*, not the address
# ---------------------------------------------------------------------------


async def _dumped_registry(topic_prefix: str | None = None) -> SchemaRegistry:
    """Build a registry the way ``schema dump`` → ``schema acl`` really does.

    The hand-written registries above key their channels *by address*, which
    is a shape the loader never produces: ``_extract_channels`` keys by the
    AsyncAPI channel name (camelCase, e.g. ``deskState``) and operations carry
    that same key as ``channel_ref`` (``_loader_helpers``: last ``$ref``
    segment).  Going through :func:`load_schema` is what makes these tests
    able to see the cos-tc2v lookup defect at all.
    """
    app = App(name="wiz2mqtt", version="1.0.0")

    @app.telemetry("desk", interval=30)
    async def _desk() -> dict[str, object]:  # pragma: no cover - never invoked
        return {}

    @app.command("lamp")
    async def _lamp(payload: str) -> None:  # pragma: no cover - never invoked
        return None

    return await load_schema(app.asyncapi(topic_prefix=topic_prefix))


class TestFindChannelResolvesChannelKeys:
    """cos-tc2v: operations reference channel *keys*, not addresses.

    Test Techniques Used:
        - Specification-based Testing: the loader's key/`$ref` contract.
        - Equivalence Partitioning: key-keyed (loader) vs address-keyed
          (hand-written) registries — both must resolve.
        - Error Guessing: a ref that matches nothing must stay unresolved.
    """

    async def test_send_channel_appears_in_publish_topics(self) -> None:
        """A dumped document's telemetry channel reaches the ACL publish list."""
        # Arrange
        registry = await _dumped_registry()

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        app_principal = next(p for p in principals if p.name == "wiz2mqtt")
        assert "wiz2mqtt/desk/state" in app_principal.publish_topics

    async def test_receive_channel_appears_in_subscribe_topics(self) -> None:
        """A dumped document's command channel reaches the ACL subscribe list."""
        # Arrange
        registry = await _dumped_registry()

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        app_principal = next(p for p in principals if p.name == "wiz2mqtt")
        assert "wiz2mqtt/lamp/set" in app_principal.subscribe_topics

    async def test_no_app_channel_is_silently_dropped(self) -> None:
        """Every channel address in the document is granted somewhere.

        The pre-fix failure was silent: only framework topics survived, so the
        broker denied every application publish.
        """
        # Arrange
        registry = await _dumped_registry()

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        app_principal = next(p for p in principals if p.name == "wiz2mqtt")
        granted = set(app_principal.publish_topics) | set(
            app_principal.subscribe_topics
        )
        assert {ch.address for ch in registry.channels.values()} <= granted

    def test_address_keyed_registry_still_resolves(self) -> None:
        """Fallback: a hand-written, address-keyed network schema keeps working."""
        # Arrange
        registry = _make_single_app_registry()

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        thermo = next(p for p in principals if p.name == "thermo2mqtt")
        assert "thermo2mqtt/temperature/state" in thermo.publish_topics


# ---------------------------------------------------------------------------
# cos-mj13.5 — framework topics are TRANSPORT, the principal name is IDENTITY
# ---------------------------------------------------------------------------


class TestAclPrefixAwareness:
    """ADR-072: granted topics follow the prefix, the principal name does not.

    Test Techniques Used:
        - Decision Table: (prefix present?) x (single/multi segment) → topics.
        - Boundary Value Analysis: no prefix, one-segment prefix, two-segment
          prefix — the depths at which a one-segment assumption breaks.
        - Specification-based Testing: the framework topic set the runtime
          actually publishes (``_health/_reporter``, ``_errors``,
          ``_schema/_validator.build_skip_topics``).
        - Round-trip Testing: unprefixed output is unchanged, byte for byte.
    """

    @staticmethod
    def _app_principal(principals: list[AclPrincipal]) -> AclPrincipal:
        return next(p for p in principals if p.name == "wiz2mqtt")

    async def test_principal_name_stays_the_app_identity(self) -> None:
        """The principal is named by ``x-cosalette-app``, never by the prefix."""
        # Arrange
        registry = await _dumped_registry("house/wiz")

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        assert {p.name for p in principals} == {"deploy", "wiz2mqtt", "monitor"}

    @pytest.mark.parametrize("prefix", ["house", "house/wiz"])
    async def test_framework_topics_use_the_resolved_prefix(self, prefix: str) -> None:
        """Status/error/_meta topics are grants for topics the app writes."""
        # Arrange
        registry = await _dumped_registry(prefix)

        # Act
        principal = self._app_principal(derive_acl_principals(registry))

        # Assert
        assert {
            f"{prefix}/status",
            f"{prefix}/error",
            f"{prefix}/schema/status",
            f"{prefix}/_meta/registry",
            f"{prefix}/_meta/state_model_drift",
            f"{prefix}/+/availability",
            f"{prefix}/+/error",
        } <= set(principal.publish_topics)

    @pytest.mark.parametrize("prefix", ["house", "house/wiz"])
    async def test_no_topic_is_granted_under_the_app_identity(
        self, prefix: str
    ) -> None:
        """Nothing is granted under ``wiz2mqtt/`` — the app never writes there."""
        # Arrange
        registry = await _dumped_registry(prefix)

        # Act
        principal = self._app_principal(derive_acl_principals(registry))

        # Assert
        granted = set(principal.publish_topics) | set(principal.subscribe_topics)
        assert not [t for t in granted if t.startswith("wiz2mqtt/")]

    @pytest.mark.parametrize("prefix", ["house", "house/wiz"])
    async def test_channel_addresses_are_granted_under_the_prefix(
        self, prefix: str
    ) -> None:
        """Channel grants follow ``channel.address``, which carries the prefix."""
        # Arrange
        registry = await _dumped_registry(prefix)

        # Act
        principal = self._app_principal(derive_acl_principals(registry))

        # Assert
        assert f"{prefix}/desk/state" in principal.publish_topics
        assert f"{prefix}/lamp/set" in principal.subscribe_topics

    async def test_unprefixed_output_is_unchanged(self) -> None:
        """Regression pin: an app with no prefix keeps every topic under its name.

        The set is pinned exactly so an accidental prefix leak shows up as a
        diff.  ``{app}/availability`` was added deliberately as a separate,
        prefix-independent fix (root entities, ADR-058); it is the only
        intentional change to this set.
        """
        # Arrange
        registry = await _dumped_registry()

        # Act
        principal = self._app_principal(derive_acl_principals(registry))

        # Assert
        assert principal == AclPrincipal(
            name="wiz2mqtt",
            publish_topics=(
                "wiz2mqtt/+/availability",
                "wiz2mqtt/+/error",
                "wiz2mqtt/_meta/registry",
                "wiz2mqtt/_meta/state_model_drift",
                "wiz2mqtt/availability",
                "wiz2mqtt/desk/state",
                "wiz2mqtt/error",
                "wiz2mqtt/schema/status",
                "wiz2mqtt/status",
            ),
            subscribe_topics=(
                "cosalette/schema/update",
                "wiz2mqtt/lamp/set",
            ),
        )

    def test_hand_written_document_without_the_extension_uses_the_app_name(
        self,
    ) -> None:
        """No ``x-cosalette-topic-prefix`` → the pre-ADR-072 app-name fallback."""
        # Arrange
        registry = _make_single_app_registry()

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        thermo = next(p for p in principals if p.name == "thermo2mqtt")
        assert "thermo2mqtt/status" in thermo.publish_topics


class TestRootDeviceAvailabilityGrant:
    """A root entity (ADR-058) publishes ``{prefix}/availability``.

    ``HealthReporter.publish_device_available(..., is_root=True)`` targets
    ``{prefix}/availability`` (``_health/_reporter.py:162``), which the
    single-segment wildcard ``{prefix}/+/availability`` does not match — so
    the broker denied it.

    Test Techniques Used:
        - Boundary Value Analysis: zero device segments vs one.
        - Specification-based Testing: against the reporter's real topics.
    """

    async def test_app_principal_may_publish_root_availability(self) -> None:
        """The root availability topic is granted alongside the wildcard one."""
        # Arrange
        registry = await _dumped_registry("house/wiz")

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        principal = next(p for p in principals if p.name == "wiz2mqtt")
        assert "house/wiz/availability" in principal.publish_topics
        assert "house/wiz/+/availability" in principal.publish_topics

    def test_monitor_may_subscribe_to_root_availability(self) -> None:
        """The fleet monitor sees root-entity availability too."""
        # Arrange
        registry = _make_single_app_registry()

        # Act
        principals = derive_acl_principals(registry)

        # Assert
        monitor = next(p for p in principals if p.name == "monitor")
        assert "+/availability" in monitor.subscribe_topics
        assert "+/+/availability" in monitor.subscribe_topics


class TestMonitorPrefixDepth:
    """The fleet monitor's wildcards must match the declared prefix depth.

    A multi-segment ``mqtt.topic_prefix`` (``house/wiz``) pushes every
    framework topic one level deeper, so fixed single-segment monitor filters
    would silently match nothing (ADR-072).

    Test Techniques Used:
        - Boundary Value Analysis: single- vs multi-segment prefixes.
        - Round-trip Testing: against a really-loaded registry.
    """

    async def test_multi_segment_prefix_monitor_covers_framework_topics(
        self,
    ) -> None:
        """A depth-2 prefix yields depth-2 monitor filters."""
        # Arrange
        registry = await _dumped_registry("house/wiz")

        # Act
        monitor = next(
            p for p in derive_acl_principals(registry) if p.name == "monitor"
        )

        # Assert — the leading prefix segment becomes two ``+`` wildcards.
        assert {
            "+/+/schema/status",
            "+/+/status",
            "+/+/error",
            "+/+/+/error",
            "+/+/availability",
            "+/+/+/availability",
            "+/+/_meta/state_model_drift",
        } == set(monitor.subscribe_topics)

    async def test_single_segment_prefix_keeps_pre_adr072_filters(self) -> None:
        """An unprefixed app collapses to the original single-segment filters."""
        # Arrange
        registry = await _dumped_registry()

        # Act
        monitor = next(
            p for p in derive_acl_principals(registry) if p.name == "monitor"
        )

        # Assert
        assert "+/status" in monitor.subscribe_topics
        assert "+/+/availability" in monitor.subscribe_topics
        assert not any(t.startswith("+/+/status") for t in monitor.subscribe_topics)
