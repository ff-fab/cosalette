"""MQTT schema validator: PayloadValidator and ValidatingMqttPort.

PayloadValidator pre-compiles JSON schema validators per channel for
pub-time validation. ValidatingMqttPort wraps an MqttPort and validates
dict payloads before publishing.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from cosalette._constants import (
    REGISTRY_TOPIC_SUFFIX,
    STATE_MODEL_DRIFT_TOPIC_SUFFIX,
)
from cosalette._mqtt import (
    ConnectCallback,
    MessageCallback,
    MqttConnectAware,
    MqttLifecycle,
    MqttMessageHandler,
    MqttPort,
)
from cosalette._schema import EnforcementConfig, SchemaRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Single validation error from JSON schema checking."""

    channel_name: str
    topic: str
    message: str
    instance_path: str


class PayloadValidator:
    """Pre-compiled JSON schema validators per channel.

    Validates dict payloads against channel schemas at publish-time.
    """

    def __init__(self, registry: SchemaRegistry) -> None:
        """Pre-compile validators for all channels with payload_schema.

        Args:
            registry: Schema registry with channels to validate against.

        Raises:
            jsonschema.SchemaError: If any payload schema is invalid.
        """
        from jsonschema import Draft7Validator as _Draft7Validator
        from jsonschema import FormatChecker

        self._validators: dict[re.Pattern[str], tuple[str, Any]] = {}

        for channel_name, channel in registry.channels.items():
            if channel.payload_schema is None:
                continue

            # Validate schema structure at construction time (fail fast)
            _Draft7Validator.check_schema(channel.payload_schema)

            # Create validator with format checking
            validator = _Draft7Validator(
                schema=channel.payload_schema,
                format_checker=FormatChecker(),
            )

            # Pre-compile topic-matching regex
            escaped = re.escape(channel.address_template)
            pattern_str = re.sub(r"\\\{[^}]+\\\}", "[^/]+", escaped)
            compiled = re.compile(f"^{pattern_str}$")

            self._validators[compiled] = (channel_name, validator)

    def validate(self, topic: str, payload: dict[str, Any]) -> list[ValidationIssue]:
        """Validate payload against matching channel schema.

        Args:
            topic: MQTT topic to validate against
            payload: Dict payload to validate

        Returns:
            List of validation issues (empty if valid or no schema matches)
        """
        # Find matching template (first match wins)
        for pattern, (channel_name, validator) in self._validators.items():
            if pattern.match(topic):
                issues = []
                for error in validator.iter_errors(payload):
                    issue = ValidationIssue(
                        channel_name=channel_name,
                        topic=topic,
                        message=error.message,
                        instance_path=".".join(str(p) for p in error.absolute_path),
                    )
                    issues.append(issue)
                return issues

        # No matching schema = no violations
        return []

    @property
    def channel_count(self) -> int:
        """Number of channels with active validators."""
        return len(self._validators)


class ValidatingMqttPort:
    """MQTT port wrapper that validates dict payloads before publishing.

    Validates dict payloads against schema registry. Enforcement modes:
    - "strict": Log error and suppress invalid publishes
    - "warn": Log warning but publish anyway
    - "off": Delegate directly without validation
    """

    def __init__(
        self,
        inner: MqttPort,
        validator: PayloadValidator,
        enforcement: EnforcementConfig,
        error_publisher: Any | None = None,  # Forward compatibility, unused
        skip_topics: frozenset[str] | None = None,
    ) -> None:
        """Initialize validating MQTT port.

        Args:
            inner: Wrapped MqttPort to delegate to
            validator: Payload validator for schema checking
            enforcement: Enforcement configuration
            error_publisher: For forward compatibility (unused in this phase)
            skip_topics: Topics to skip validation (e.g. error topics)
        """
        self._inner = inner
        self._validator = validator
        self._enforcement = enforcement
        self._error_publisher = error_publisher
        self._skip_topics = skip_topics or frozenset()
        self._violation_count = 0

    @property
    def violation_count(self) -> int:
        """Number of schema violations encountered."""
        return self._violation_count

    async def publish(
        self,
        topic: str,
        payload: str | dict[str, Any],
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Publish payload after schema validation.

        Validates dict payloads against schema. String payloads and
        skip_topics bypass validation.
        """
        # Skip validation in off mode
        if self._enforcement.mode == "off":
            await self._inner.publish(topic, payload, retain=retain, qos=qos)
            return

        # Skip validation for string payloads (already serialized)
        if isinstance(payload, str):
            await self._inner.publish(topic, payload, retain=retain, qos=qos)
            return

        # Skip validation for designated topics
        if topic in self._skip_topics:
            await self._inner.publish(topic, payload, retain=retain, qos=qos)
            return

        # Validate dict payload
        issues = self._validator.validate(topic, payload)

        if issues:
            self._violation_count += 1
            issue_count = len(issues)

            if self._enforcement.mode == "strict":
                logger.error(
                    "Schema violation on %s: %d issue(s) — publish suppressed",
                    topic,
                    issue_count,
                )
                # Log individual issues at debug level
                for issue in issues:
                    logger.debug(
                        "  - %s (path: %s)", issue.message, issue.instance_path
                    )

                # TODO: Publish error payload in future phase
                return  # Suppress publish

            elif self._enforcement.mode == "warn":
                logger.warning(
                    "Schema violation on %s: %d issue(s) — publishing anyway",
                    topic,
                    issue_count,
                )
                # Log individual issues at debug level
                for issue in issues:
                    logger.debug(
                        "  - %s (path: %s)", issue.message, issue.instance_path
                    )

                # TODO: Publish error payload in future phase
                # Fall through to publish anyway

        # Delegate to inner (valid payload or warn mode)
        await self._inner.publish(topic, payload, retain=retain, qos=qos)

    async def subscribe(self, topic: str) -> None:
        """Delegate subscription to inner port."""
        await self._inner.subscribe(topic)

    def on_message(self, callback: MessageCallback) -> None:
        """Delegate message callback registration to inner port."""
        if isinstance(self._inner, MqttMessageHandler):
            self._inner.on_message(callback)

    async def start(self) -> None:
        """Delegate lifecycle start to inner port if supported."""
        if isinstance(self._inner, MqttLifecycle):
            await self._inner.start()

    async def stop(self) -> None:
        """Delegate lifecycle stop to inner port if supported."""
        if isinstance(self._inner, MqttLifecycle):
            await self._inner.stop()

    def reload(self, registry: SchemaRegistry) -> None:
        """Hot-reload schema validators from a new registry.

        Safe under asyncio's cooperative scheduling — no concurrent
        publish can observe a half-swapped state.
        """
        self._validator = PayloadValidator(registry)
        self._enforcement = registry.enforcement
        self._violation_count = 0
        logger.info(
            "Schema reloaded — %d channel validators active",
            self._validator.channel_count,
        )


class _ConnectAwareValidatingMqttPort(ValidatingMqttPort):
    """Validating port variant for connect-aware inner adapters.

    Selected by :func:`build_validating_port` only when the inner port
    implements :class:`MqttConnectAware`. Defining ``add_connect_callback``
    on a dedicated subclass keeps ``isinstance(port, MqttConnectAware)``
    truthful under ``@runtime_checkable`` structural checks (PEP 544): a
    non-connect-aware inner yields the plain base wrapper instead, so the
    framework's eager-startup announce path is not silently disabled.
    """

    def add_connect_callback(self, callback: ConnectCallback) -> None:
        """Delegate connect-callback registration to the inner port."""
        cast(MqttConnectAware, self._inner).add_connect_callback(callback)

    @property
    def is_connected(self) -> bool:
        """Reflect the inner port's connection state (``False`` if unknown)."""
        return bool(getattr(self._inner, "is_connected", False))


def build_validating_port(
    inner: MqttPort,
    validator: PayloadValidator,
    enforcement: EnforcementConfig,
    *,
    error_publisher: Any | None = None,
    skip_topics: frozenset[str] | None = None,
) -> ValidatingMqttPort:
    """Build a validating port whose capability surface mirrors *inner*.

    Returns a connect-aware variant when *inner* implements
    :class:`MqttConnectAware`, so runtime capability checks stay truthful.
    Otherwise the F-1/F-2 reconnect reannounce hook would silently never
    register under schema enforcement — and unconditionally exposing
    ``add_connect_callback`` on the base wrapper would instead make *every*
    wrapped adapter falsely connect-aware, disabling the eager startup
    announce for mock/null adapters.

    See Also:
        ADR-033 — MQTT schema enforcement.
        ADR-006 — Interface Segregation (narrow, truthful capability ports).
    """
    cls = (
        _ConnectAwareValidatingMqttPort
        if isinstance(inner, MqttConnectAware)
        else ValidatingMqttPort
    )
    return cls(
        inner=inner,
        validator=validator,
        enforcement=enforcement,
        error_publisher=error_publisher,
        skip_topics=skip_topics,
    )


def build_skip_topics(prefix: str, device_names: frozenset[str]) -> frozenset[str]:
    """Build set of topics to skip validation.

    Includes error/status topics and other framework-managed topics
    that should not be validated.

    Args:
        prefix: MQTT prefix (app name)
        device_names: Set of device names for per-device error topics

    Returns:
        Set of topics to skip during validation
    """
    skip = {
        f"{prefix}/error",
        f"{prefix}/status",
        f"{prefix}/schema/status",
        f"{prefix}/{REGISTRY_TOPIC_SUFFIX}",
        f"{prefix}/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}",
    }

    for name in device_names:
        skip.add(f"{prefix}/{name}/error")
        skip.add(f"{prefix}/{name}/availability")

    return frozenset(skip)


@dataclass
class SchemaStatusPublisher:
    """Publishes schema compliance status to {prefix}/schema/status.

    Fire-and-forget: publication errors are logged but never propagated.
    """

    _mqtt: MqttPort
    _topic_prefix: str
    _enforcement_mode: str
    _validating_port: ValidatingMqttPort | None = field(default=None, repr=False)

    async def publish_status(self) -> None:
        """Publish current schema status as retained JSON."""
        violation_count = (
            self._validating_port.violation_count if self._validating_port else 0
        )
        status = "compliant" if violation_count == 0 else "violations_detected"
        payload: dict[str, Any] = {
            "enforcement": self._enforcement_mode,
            "violation_count": violation_count,
            "status": status,
        }
        topic = f"{self._topic_prefix}/schema/status"
        try:
            await self._mqtt.publish(topic, payload, retain=True, qos=1)
        except Exception:
            logger.exception("Failed to publish schema status to %s", topic)
