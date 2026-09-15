"""``@app.inbound()`` decorator and mixin."""

from __future__ import annotations

import logging
import re
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._injection import build_injection_plan, detect_raw_mqtt_params
from cosalette._registration import (
    EnabledSpec,
    NameSpec,
    TopicSpec,
    _InboundRegistration,
    validate_mqtt_name,
)
from cosalette._runners._stream_types import BackpressurePolicy
from cosalette._utils import _callable_name

logger = logging.getLogger(__name__)

_WILDCARD_RE = re.compile(r"[+#]")


def _validate_inbound_topic(topic: str) -> None:
    """Reject wildcard characters and empty topics."""
    if not topic:
        msg = "Inbound topic must not be empty"
        raise ValueError(msg)
    if _WILDCARD_RE.search(topic):
        msg = f"Inbound topic must not contain wildcards (+/#), got {topic!r}"
        raise ValueError(msg)


class _InboundMixin:
    """Mixin that provides ``@app.inbound()`` to :class:`App`."""

    _inbounds: list[_InboundRegistration]

    @property
    @abstractmethod
    def registered_names(self) -> frozenset[str]: ...

    def _validate_inbound_duplicate(self, name: str, topic: str | None) -> None:
        """Reject concrete duplicates while preserving deferred enabled semantics."""
        for reg in self._inbounds:
            if reg.enabled_spec is not True:
                continue
            if reg.name == name:
                msg = f"Inbound name {name!r} is already registered"
                raise ValueError(msg)
            if topic is not None and reg.topic == topic:
                msg = f"Inbound topic {topic!r} is already registered"
                raise ValueError(msg)

    def inbound(
        self,
        name: str | NameSpec | None = None,
        *,
        topic: TopicSpec,
        enabled: EnabledSpec = True,
        summary: str | None = None,
        payload_model: type | None = None,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> Callable[..., Any]:
        """Register a handler for an external MQTT topic."""
        if callable(enabled):
            return self._make_deferred_inbound_decorator(
                name,
                topic,
                enabled,
                summary,
                payload_model,
                maxsize,
                backpressure,
                behavior,
                effects,
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func
            effective_name = name if isinstance(name, str) else None
            if effective_name is None and not callable(name):
                effective_name = _callable_name(func)
            self.add_inbound(
                effective_name if effective_name else name or _callable_name(func),
                func,
                topic=topic,
                enabled=enabled,
                summary=summary,
                payload_model=payload_model,
                maxsize=maxsize,
                backpressure=backpressure,
                behavior=behavior,
                effects=effects,
            )
            return func

        return decorator

    def _make_deferred_inbound_decorator(
        self,
        name: str | NameSpec | None,
        topic: TopicSpec,
        enabled: EnabledSpec,
        summary: str | None,
        payload_model: type | None,
        maxsize: int,
        backpressure: BackpressurePolicy,
        behavior: list[str] | None,
        effects: list[str] | None,
    ) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            resolved_name = name if isinstance(name, str) else _callable_name(func)
            if isinstance(resolved_name, str):
                validate_mqtt_name(resolved_name)
            raw_mqtt = detect_raw_mqtt_params(func)
            plan = build_injection_plan(func, mqtt_params=raw_mqtt)
            topic_str: str | None = topic if isinstance(topic, str) else None
            topic_spec_val: TopicSpec | None = topic if callable(topic) else None
            if topic_str is not None:
                _validate_inbound_topic(topic_str)
            self._inbounds.append(
                _InboundRegistration(
                    name=resolved_name
                    if isinstance(resolved_name, str)
                    else func.__name__,
                    func=func,
                    injection_plan=plan,
                    mqtt_params=raw_mqtt,
                    enabled_spec=enabled,
                    name_spec=None if isinstance(name, str) else name,
                    summary=summary,
                    payload_model=payload_model,
                    behavior=behavior,
                    effects=effects,
                    topic=topic_str,
                    topic_spec=topic_spec_val,
                    maxsize=maxsize,
                    backpressure=backpressure,
                ),
            )
            return func

        return decorator

    def add_inbound(
        self,
        name: str | NameSpec,
        func: Callable[..., Any],
        *,
        topic: TopicSpec,
        enabled: bool = True,
        summary: str | None = None,
        payload_model: type | None = None,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> None:
        """Register an inbound handler imperatively."""
        if not enabled:
            return
        raw_mqtt = detect_raw_mqtt_params(func)
        plan = build_injection_plan(func, mqtt_params=raw_mqtt)
        name_spec: NameSpec | None = None
        if isinstance(name, str):
            resolved_name = name
            validate_mqtt_name(resolved_name)
        else:
            name_spec = name
            resolved_name = _callable_name(func)
        topic_str: str | None = topic if isinstance(topic, str) else None
        topic_spec_val: TopicSpec | None = topic if callable(topic) else None
        if topic_str is not None:
            _validate_inbound_topic(topic_str)
        if name_spec is None:
            self._validate_inbound_duplicate(resolved_name, topic_str)
        self._inbounds.append(
            _InboundRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                mqtt_params=raw_mqtt,
                enabled_spec=enabled,
                name_spec=name_spec,
                summary=summary,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                topic=topic_str,
                topic_spec=topic_spec_val,
                maxsize=maxsize,
                backpressure=backpressure,
            ),
        )
