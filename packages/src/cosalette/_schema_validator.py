"""MQTT schema validator: PayloadValidator and ValidatingMqttPort.

PayloadValidator pre-compiles JSON schema validators per channel for
pub-time validation. ValidatingMqttPort wraps an MqttPort and validates
dict payloads before publishing.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from cosalette._mqtt import MessageCallback, MqttPort
from cosalette._schema import EnforcementConfig, SchemaRegistry, _topic_matches

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Single validation error from JSON schema checking."""

    channel_name: str
    topic: str
    message: str
    schema_path: str


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
        self._validators: dict[str, tuple[str, Draft7Validator]] = {}

        for channel_name, channel in registry.channels.items():
            if channel.payload_schema is None:
                continue

            # Validate schema structure at construction time (fail fast)
            Draft7Validator.check_schema(channel.payload_schema)

            # Create validator with format checking
            validator = Draft7Validator(
                schema=channel.payload_schema,
                format_checker=FormatChecker(),
            )

            self._validators[channel.address_template] = (channel_name, validator)

    def validate(self, topic: str, payload: dict[str, Any]) -> list[ValidationIssue]:
        """Validate payload against matching channel schema.

        Args:
            topic: MQTT topic to validate against
            payload: Dict payload to validate

        Returns:
            List of validation issues (empty if valid or no schema matches)
        """
        # Find matching template (first match wins)
        for template, (channel_name, validator) in self._validators.items():
            if _topic_matches(template, topic):
                issues = []
                for error in validator.iter_errors(payload):
                    issue = ValidationIssue(
                        channel_name=channel_name,
                        topic=topic,
                        message=error.message,
                        schema_path=".".join(str(p) for p in error.absolute_path),
                    )
                    issues.append(issue)
                return issues

        # No matching schema = no violations
        return []


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
                    logger.debug("  - %s (path: %s)", issue.message, issue.schema_path)

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
                    logger.debug("  - %s (path: %s)", issue.message, issue.schema_path)

                # TODO: Publish error payload in future phase
                # Fall through to publish anyway

        # Delegate to inner (valid payload or warn mode)
        await self._inner.publish(topic, payload, retain=retain, qos=qos)

    async def subscribe(self, topic: str) -> None:
        """Delegate subscription to inner port."""
        await self._inner.subscribe(topic)

    def on_message(self, callback: MessageCallback) -> None:
        """Delegate message callback registration to inner port."""
        if hasattr(self._inner, "on_message"):
            self._inner.on_message(callback)

    async def start(self) -> None:
        """Delegate lifecycle start to inner port if supported."""
        if hasattr(self._inner, "start"):
            await self._inner.start()

    async def stop(self) -> None:
        """Delegate lifecycle stop to inner port if supported."""
        if hasattr(self._inner, "stop"):
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
            len(self._validator._validators),
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
        f"{prefix}/_meta/registry",
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
