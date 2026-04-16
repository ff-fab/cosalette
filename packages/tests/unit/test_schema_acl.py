"""Unit tests for cosalette._schema_acl — ACL generation from AsyncAPI schemas.

Test Techniques Used:
- Specification-based Testing: derive_acl_principals and formatter contracts
- Equivalence Partitioning: deploy/per-app/monitor principal types, broker formats
- Branch Coverage: publish vs subscribe directions, wildcard handling
- Round-trip Testing: formatter output structure verification
"""

from __future__ import annotations

import pytest

from cosalette._schema import (
    ChannelSchema,
    EnforcementConfig,
    OperationSchema,
    SchemaRegistry,
)
from cosalette._schema._acl import FORMATTERS, AclPrincipal, derive_acl_principals

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
        """Single-app mode with app_prefix only creates deploy + that app + monitor."""
        registry = _make_network_registry()
        principals = derive_acl_principals(registry, app_prefix="thermo2mqtt")

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
            principal.name = "modified"  # type: ignore[misc]

        # Tuples should be immutable too
        assert isinstance(principal.publish_topics, tuple)
        assert isinstance(principal.subscribe_topics, tuple)
