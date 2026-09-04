"""ACL generation for MQTT brokers from AsyncAPI schemas.

Generates broker-specific ACL configurations by extracting channel topics and
their directions from schema registries.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

from cosalette._constants import (
    REGISTRY_TOPIC_SUFFIX,
    STATE_MODEL_DRIFT_TOPIC_SUFFIX,
)
from cosalette._schema import ChannelSchema, SchemaRegistry

# Safe characters for ACL principal names and topic segments.
# Rejects control chars, newlines, quotes, broker metacharacters.
_SAFE_ACL_RE = re.compile(r"^[A-Za-z0-9_./:{}+#-]+$")


def _validate_acl_value(value: str, label: str) -> None:
    """Raise ValueError if *value* contains unsafe ACL characters."""
    if not _SAFE_ACL_RE.match(value):
        msg = f"Unsafe characters in {label}: {value!r}"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AclPrincipal:
    """A principal (user/client) with topic permissions."""

    name: str
    publish_topics: tuple[str, ...]
    subscribe_topics: tuple[str, ...]


def _find_channel(
    registry: SchemaRegistry,
    channel_ref: str,
) -> ChannelSchema | None:
    """Find a channel by reference in the registry."""
    for ch in registry.channels.values():
        if channel_ref == ch.address or channel_ref.endswith(ch.address):
            return ch
    return None


def _build_app_principal(
    app_name: str,
    registry: SchemaRegistry,
) -> AclPrincipal:
    """Build an ACL principal for a single app."""
    _validate_acl_value(app_name, "app name")

    publish_topics = [
        f"{app_name}/status",
        f"{app_name}/error",
        f"{app_name}/schema/status",
        f"{app_name}/{REGISTRY_TOPIC_SUFFIX}",
        f"{app_name}/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}",
        f"{app_name}/+/availability",
        f"{app_name}/+/error",
    ]
    subscribe_topics: list[str] = ["cosalette/schema/update"]

    for operation in registry.operations.values():
        channel = _find_channel(registry, operation.channel_ref)
        if not channel:
            continue
        if channel.app_name != app_name and channel.scope != "all_apps":
            continue

        if operation.action == "send":
            _validate_acl_value(channel.address, "channel address")
            publish_topics.append(channel.address)
        elif operation.action == "receive":
            addr = channel.address
            if "{deviceName}" in addr:
                addr = addr.replace("{deviceName}", "+")
            _validate_acl_value(addr, "channel address")
            subscribe_topics.append(addr)

    return AclPrincipal(
        name=app_name,
        publish_topics=tuple(sorted(set(publish_topics))),
        subscribe_topics=tuple(sorted(set(subscribe_topics))),
    )


def derive_acl_principals(
    registry: SchemaRegistry,
    app_prefix: str | None = None,
) -> list[AclPrincipal]:
    """Create ACL principals from schema registry.

    Creates 3 types of principals:
    1. **deploy** — readwrite on ALL topics (wildcard #). Admin/deployment.
    2. **Per-app principals** — For each app found in channels:
       - Publish: "send" direction channels + framework topics
       - Subscribe: "receive" direction channels + schema updates
    3. **monitor** — Subscribe-only on status/error/availability topics plus
       the ADR-069 `_meta/state_model_drift` fleet-scrape topic

    Args:
        registry: The schema registry to extract channels from.
        app_prefix: If provided, only create principals for this app.

    Returns:
        List of ACL principals.
    """
    principals = [
        AclPrincipal(
            name="deploy",
            publish_topics=("#",),
            subscribe_topics=("#",),
        )
    ]

    app_names = {app_prefix} if app_prefix else registry.all_app_names()
    for app_name in app_names:
        principals.append(_build_app_principal(app_name, registry))

    # 3. Monitor principal - subscribe-only
    monitor_topics = [
        "+/schema/status",
        "+/status",
        "+/error",
        "+/+/error",
        "+/+/availability",
        f"+/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}",
    ]

    principals.append(
        AclPrincipal(
            name="monitor",
            publish_topics=(),
            subscribe_topics=tuple(monitor_topics),
        )
    )

    return principals


def _format_acl_body(principals: list[AclPrincipal]) -> list[str]:
    """Format ACL rules as Mosquitto/VerneMQ body lines (no header)."""
    lines: list[str] = []
    for principal in principals:
        lines.append(f"user {principal.name}")

        if principal.name == "deploy" and "#" in principal.publish_topics:
            lines.append("topic readwrite #")
        else:
            for topic in principal.publish_topics:
                lines.append(f"topic write {topic}")
            for topic in principal.subscribe_topics:
                lines.append(f"topic read {topic}")

        lines.append("")
    return lines


def format_mosquitto(principals: list[AclPrincipal]) -> str:
    """Format principals as Mosquitto ACL file."""
    lines = [
        "# Generated by cosalette schema acl",
        "# Broker: Mosquitto",
        "",
    ]
    lines.extend(_format_acl_body(principals))
    return "\n".join(lines)


def format_emqx(principals: list[AclPrincipal]) -> str:
    """Format principals as EMQX Erlang ACL rules."""
    lines = [
        "%% Generated by cosalette schema acl",
        "%% Broker: EMQX",
        "",
    ]

    for principal in principals:
        name = principal.name

        # Handle deploy special case
        if name == "deploy" and "#" in principal.publish_topics:
            lines.append(f'{{allow, {{user, "{name}"}}, all, ["#"]}}.')
        else:
            # Publish permissions
            if principal.publish_topics:
                topics_str = ", ".join(
                    f'"{topic}"' for topic in principal.publish_topics
                )
                lines.append(f'{{allow, {{user, "{name}"}}, publish, [{topics_str}]}}.')

            # Subscribe permissions
            if principal.subscribe_topics:
                topics_str = ", ".join(
                    f'"{topic}"' for topic in principal.subscribe_topics
                )
                lines.append(
                    f'{{allow, {{user, "{name}"}}, subscribe, [{topics_str}]}}.'
                )

    lines.extend(["", "{deny, all}."])
    return "\n".join(lines)


def format_hivemq(principals: list[AclPrincipal]) -> str:
    """Format principals as HiveMQ XML configuration."""

    # Create root element
    root = ET.Element("file-rbac")
    root.insert(0, ET.Comment(" Generated by cosalette schema acl "))
    root.insert(1, ET.Comment(" Broker: HiveMQ "))

    # Users section
    users_elem = ET.SubElement(root, "users")

    # Roles section
    roles_elem = ET.SubElement(root, "roles")

    for principal in principals:
        # User element
        user_elem = ET.SubElement(users_elem, "user")
        name_elem = ET.SubElement(user_elem, "name")
        name_elem.text = principal.name

        roles_elem_user = ET.SubElement(user_elem, "roles")
        id_elem = ET.SubElement(roles_elem_user, "id")
        id_elem.text = principal.name

        # Role element
        role_elem = ET.SubElement(roles_elem, "role")
        role_id_elem = ET.SubElement(role_elem, "id")
        role_id_elem.text = principal.name

        permissions_elem = ET.SubElement(role_elem, "permissions")

        # Handle deploy special case
        if principal.name == "deploy" and "#" in principal.publish_topics:
            perm_elem = ET.SubElement(permissions_elem, "permission")
            topic_elem = ET.SubElement(perm_elem, "topic")
            topic_elem.text = "#"
        else:
            # Publish permissions
            for topic in principal.publish_topics:
                perm_elem = ET.SubElement(permissions_elem, "permission")
                topic_elem = ET.SubElement(perm_elem, "topic")
                topic_elem.text = topic
                activity_elem = ET.SubElement(perm_elem, "activity")
                activity_elem.text = "PUBLISH"

            # Subscribe permissions
            for topic in principal.subscribe_topics:
                perm_elem = ET.SubElement(permissions_elem, "permission")
                topic_elem = ET.SubElement(perm_elem, "topic")
                topic_elem.text = topic
                activity_elem = ET.SubElement(perm_elem, "activity")
                activity_elem.text = "SUBSCRIBE"

    # Convert to string with XML declaration
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def format_vernemq(principals: list[AclPrincipal]) -> str:
    """Format principals as VerneMQ ACL file."""
    lines = [
        "# Generated by cosalette schema acl",
        "# Broker: VerneMQ",
        "",
    ]
    lines.extend(_format_acl_body(principals))
    return "\n".join(lines)


def _nanomq_rule(
    username: str,
    action: str,
    topics: list[str],
    *,
    permit: str = "allow",
) -> str:
    """Build a single NanoMQ JSON rule string."""
    rule = {
        "permit": permit,
        "username": username,
        "action": action,
        "topics": topics,
    }
    return json.dumps(rule)


def format_nanomq(principals: list[AclPrincipal]) -> str:
    """Format principals as NanoMQ JSON-style rules."""
    lines = [
        "# Generated by cosalette schema acl",
        "# Broker: NanoMQ",
        "",
        "rules = [",
    ]

    for principal in principals:
        name = principal.name

        # Handle deploy special case
        if name == "deploy" and "#" in principal.publish_topics:
            rule = _nanomq_rule(name, "pubsub", ["#"])
            lines.append(f"    {rule},")
        else:
            if principal.publish_topics:
                rule = _nanomq_rule(name, "publish", list(principal.publish_topics))
                lines.append(f"    {rule},")

            if principal.subscribe_topics:
                rule = _nanomq_rule(name, "subscribe", list(principal.subscribe_topics))
                lines.append(f"    {rule},")

    deny = _nanomq_rule("#", "pubsub", ["#"], permit="deny")
    lines.append(f"    {deny}")
    lines.append("]")

    return "\n".join(lines)


# Registry of available formatters
FORMATTERS: dict[str, Callable[[list[AclPrincipal]], str]] = {
    "mosquitto": format_mosquitto,
    "emqx": format_emqx,
    "hivemq": format_hivemq,
    "vernemq": format_vernemq,
    "nanomq": format_nanomq,
}
