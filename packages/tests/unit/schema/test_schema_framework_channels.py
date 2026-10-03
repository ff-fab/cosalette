"""Framework availability channels through the schema toolchain (ADR-086).

The generator adds one ``x-cosalette-framework: "availability"`` channel per
entity that owns availability. Downstream tools must treat them as framework
plumbing: the loader round-trips the role, while consumer generation (Home
Assistant / openHAB), the broker ACL and device-name extraction produce
exactly the output they produced before the channels existed.

Test Techniques Used:
    - Round-trip Testing: ``asyncapi()`` → ``load_schema`` → slice re-emit.
    - Equivalence Partitioning: framework vs app-owned channels.
    - Error Guessing: a malformed ``x-cosalette-framework`` value.
    - Regression Testing (differential): every consumer artefact generated
      from the document with and without the framework channels is
      byte-identical.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, Field

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._schema import X_COSALETTE_FRAMEWORK, SchemaRegistry
from cosalette._schema._acl import FORMATTERS, derive_acl_principals
from cosalette._schema._asyncapi import _registry_to_asyncapi_dict
from cosalette._schema._consumer_gen import (
    HaDiscoveryGenerator,
    OpenHabGenerator,
    _is_consumer_visible,
    ha_discovery_to_json,
    silent_consumer_channels,
)
from cosalette._schema._loader import SchemaLoadError, load_schema
from cosalette.schema import consumer

pytestmark = pytest.mark.unit


class _Reading(BaseModel):
    """A consumer-annotated state model."""

    celsius: Annotated[
        float,
        Field(
            json_schema_extra=consumer(
                display_name="Temperature",
                device_class="temperature",
                unit="°C",
                state_class="measurement",
            )
        ),
    ]


class _Switch(BaseModel):
    """A consumer-annotated command payload."""

    on: Annotated[bool, Field(json_schema_extra=consumer(display_name="Power"))]


def _app() -> App:
    """Root, named, shared-name and non-discoverable entities in one app."""
    app = App(name="wiz2mqtt", version="1.0.0")

    @app.telemetry(interval=30, state_model=_Reading)
    async def _root() -> _Reading:  # pragma: no cover - never invoked
        return _Reading(celsius=20.0)

    @app.telemetry("desk", interval=30, state_model=_Reading)
    async def _desk() -> _Reading:  # pragma: no cover - never invoked
        return _Reading(celsius=21.5)

    @app.device("lamp", state_model=_Reading, payload_model=_Switch)
    async def _lamp(ctx: DeviceContext) -> None:  # pragma: no cover
        return None

    @app.command("desk")
    async def _desk_cmd(payload: _Switch) -> None:  # pragma: no cover
        return None

    @app.telemetry("events", interval=30, discoverable=False)
    async def _events() -> dict[str, Any]:  # pragma: no cover - never invoked
        return {}

    return app


def _without_framework_channels(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *doc* as it looked before ADR-086."""
    stripped = copy.deepcopy(doc)
    dropped = {
        cid for cid, ch in stripped["channels"].items() if X_COSALETTE_FRAMEWORK in ch
    }
    for cid in dropped:
        del stripped["channels"][cid]
    stripped["operations"] = {
        name: op
        for name, op in stripped["operations"].items()
        if op["channel"]["$ref"].rsplit("/", 1)[-1] not in dropped
    }
    return stripped


@pytest.fixture(params=[None, "house/wiz"], ids=["unprefixed", "prefixed"])
def topic_prefix(request: pytest.FixtureRequest) -> str | None:
    """Both the identity-equals-prefix case and a multi-segment prefix."""
    value = request.param
    assert value is None or isinstance(value, str)
    return value


async def _pair(topic_prefix: str | None) -> tuple[SchemaRegistry, SchemaRegistry]:
    """Load the document with and without its framework channels."""
    doc = _app().asyncapi(topic_prefix=topic_prefix)
    assert any(X_COSALETTE_FRAMEWORK in ch for ch in doc["channels"].values())
    with_framework = await load_schema(copy.deepcopy(doc))
    without_framework = await load_schema(_without_framework_channels(doc))
    return with_framework, without_framework


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoaderRoundTrip:
    """``x-cosalette-framework`` parses into ``framework_role`` and back.

    Technique: Round-trip Testing and Error Guessing.
    """

    async def test_role_parsed_into_framework_role(self) -> None:
        # Arrange
        doc = _app().asyncapi()

        # Act
        registry = await load_schema(doc)

        # Assert
        roles = {
            name: ch.framework_role
            for name, ch in registry.channels.items()
            if ch.framework_role is not None
        }
        assert roles == {
            "availability": "availability",
            "deskAvailability": "availability",
            "eventsAvailability": "availability",
            "lampAvailability": "availability",
        }

    async def test_framework_channel_is_not_discoverable(self) -> None:
        # Arrange / Act
        registry = await load_schema(_app().asyncapi())

        # Assert
        channel = registry.channels["deskAvailability"]
        assert channel.discoverable is False
        assert channel.archetype is None
        assert channel.app_name == "wiz2mqtt"

    async def test_app_channels_have_no_role(self) -> None:
        # Arrange / Act
        registry = await load_schema(_app().asyncapi())

        # Assert
        assert registry.channels["deskState"].framework_role is None

    async def test_slice_re_emits_the_role(self) -> None:
        # Arrange
        registry = await load_schema(_app().asyncapi())

        # Act
        re_emitted = _registry_to_asyncapi_dict(registry)
        reloaded = await load_schema(re_emitted)

        # Assert
        assert re_emitted["channels"]["deskAvailability"][X_COSALETTE_FRAMEWORK] == (
            "availability"
        )
        assert reloaded.channels["deskAvailability"].framework_role == "availability"
        assert X_COSALETTE_FRAMEWORK not in re_emitted["channels"]["deskState"]

    @pytest.mark.parametrize(
        "key",
        [X_COSALETTE_FRAMEWORK, "x-cosalette-app", "x-cosalette-coalescing-group"],
    )
    @pytest.mark.parametrize("bad", [None, "", "   ", 1, True, ["availability"], {}])
    async def test_malformed_string_extension_is_rejected(
        self, key: str, bad: object
    ) -> None:
        """A present optional string key rejects null and every invalid value."""
        # Arrange
        doc = copy.deepcopy(_app().asyncapi())
        doc["channels"]["deskAvailability"][key] = bad

        # Act / Assert
        with pytest.raises(SchemaLoadError, match=key):
            await load_schema(doc)

    @pytest.mark.parametrize(
        ("key", "attribute"),
        [
            (X_COSALETTE_FRAMEWORK, "framework_role"),
            ("x-cosalette-app", "app_name"),
            ("x-cosalette-coalescing-group", "coalescing_group"),
        ],
    )
    @pytest.mark.parametrize("value", [None, "future-role"], ids=["omitted", "valid"])
    async def test_optional_string_extension_accepts_omission_or_nonempty_string(
        self, key: str, attribute: str, value: str | None
    ) -> None:
        """Missing keys retain None; nonempty strings round-trip verbatim."""
        # Arrange
        doc = copy.deepcopy(_app().asyncapi())
        channel = doc["channels"]["deskAvailability"]
        if value is None:
            channel.pop(key, None)
        else:
            channel[key] = value

        # Act
        registry = await load_schema(doc)

        # Assert
        assert getattr(registry.channels["deskAvailability"], attribute) == value


# ---------------------------------------------------------------------------
# Consumer generation
# ---------------------------------------------------------------------------


class TestConsumerGenerationExcludesFrameworkChannels:
    """Framework channels are invisible to HA / openHAB generation.

    Technique: Equivalence Partitioning and differential Regression Testing.
    """

    async def test_framework_channels_are_not_consumer_visible(self) -> None:
        # Arrange
        registry = await load_schema(_app().asyncapi())

        # Act
        visible = {
            name for name, ch in registry.channels.items() if _is_consumer_visible(ch)
        }

        # Assert
        assert not {n for n in visible if n.endswith("Availability")}
        assert "availability" not in visible

    async def test_visibility_ignores_discoverable_flag(self) -> None:
        """A framework channel stays hidden even if a document says discoverable."""
        # Arrange
        doc = copy.deepcopy(_app().asyncapi())
        del doc["channels"]["deskAvailability"]["x-cosalette-discoverable"]

        # Act
        registry = await load_schema(doc)

        # Assert
        assert not _is_consumer_visible(registry.channels["deskAvailability"])

    @pytest.mark.parametrize("ha_composites_emit", [True, False])
    async def test_silent_channels_unchanged(
        self, topic_prefix: str | None, ha_composites_emit: bool
    ) -> None:
        # Arrange
        with_framework, without_framework = await _pair(topic_prefix)

        # Act
        silent = silent_consumer_channels(
            with_framework, ha_composites_emit=ha_composites_emit
        )
        baseline = silent_consumer_channels(
            without_framework, ha_composites_emit=ha_composites_emit
        )

        # Assert
        assert silent == baseline
        assert not [s for s in silent if "vailability" in s.name]

    async def test_ha_discovery_output_byte_identical(
        self, topic_prefix: str | None
    ) -> None:
        # Arrange
        with_framework, without_framework = await _pair(topic_prefix)

        # Act
        actual = ha_discovery_to_json(
            HaDiscoveryGenerator(registry=with_framework).generate()
        )
        expected = ha_discovery_to_json(
            HaDiscoveryGenerator(registry=without_framework).generate()
        )

        # Assert
        assert actual == expected
        assert "/desk/availability" in actual  # HA keeps its availability list

    async def test_openhab_output_byte_identical(
        self, topic_prefix: str | None
    ) -> None:
        # Arrange
        with_framework, without_framework = await _pair(topic_prefix)

        # Act
        actual = OpenHabGenerator(registry=with_framework)
        expected = OpenHabGenerator(registry=without_framework)

        # Assert
        assert actual.generate_things() == expected.generate_things()
        assert actual.generate_items() == expected.generate_items()


# ---------------------------------------------------------------------------
# Broker ACL
# ---------------------------------------------------------------------------


class TestAclUnchanged:
    """The fixed availability grants already cover the framework channels.

    Technique: differential Regression Testing across every broker format.
    """

    async def test_principals_unchanged(self, topic_prefix: str | None) -> None:
        # Arrange
        with_framework, without_framework = await _pair(topic_prefix)

        # Act
        actual = derive_acl_principals(with_framework)
        expected = derive_acl_principals(without_framework)

        # Assert
        assert actual == expected

    @pytest.mark.parametrize("broker", sorted(FORMATTERS))
    async def test_formatted_acl_byte_identical(
        self, topic_prefix: str | None, broker: str
    ) -> None:
        # Arrange
        with_framework, without_framework = await _pair(topic_prefix)
        formatter = FORMATTERS[broker]

        # Act
        actual = formatter(derive_acl_principals(with_framework))
        expected = formatter(derive_acl_principals(without_framework))

        # Assert
        assert actual == expected

    @pytest.mark.parametrize("action", ["send", "receive"])
    async def test_unknown_framework_role_retains_operation_grant(
        self, action: str
    ) -> None:
        """Future roles need ACL grants for addresses outside fixed permissions."""
        # Arrange
        doc = copy.deepcopy(_app().asyncapi())
        address = "external/framework/diagnostics"
        doc["channels"]["deskAvailability"].update(
            {"address": address, X_COSALETTE_FRAMEWORK: "future-role"}
        )
        doc["operations"]["publishDeskAvailability"]["action"] = action
        registry = await load_schema(doc)

        # Act
        principal = next(
            p for p in derive_acl_principals(registry) if p.name == "wiz2mqtt"
        )

        # Assert
        assert registry.channels["deskAvailability"].framework_role == "future-role"
        assert (address in principal.publish_topics) is (action == "send")
        assert (address in principal.subscribe_topics) is (action == "receive")


# ---------------------------------------------------------------------------
# Device names (enforcement, validator skip topics)
# ---------------------------------------------------------------------------


class TestDeviceNamesUnchanged:
    """Framework channels carry no archetype, so they add no device names.

    Technique: differential Regression Testing.
    """

    async def test_device_names_unchanged(self, topic_prefix: str | None) -> None:
        # Arrange
        with_framework, without_framework = await _pair(topic_prefix)

        # Act / Assert
        assert with_framework.device_names == without_framework.device_names
        assert "desk" in with_framework.device_names

    async def test_filtered_device_names_unchanged(self) -> None:
        # Arrange
        with_framework, without_framework = await _pair(None)

        # Act
        actual = with_framework.filter_for_app("wiz2mqtt").device_names
        expected = without_framework.filter_for_app("wiz2mqtt").device_names

        # Assert
        assert actual == expected
