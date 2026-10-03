"""Regression tests for stream rootness and router feed reference scope.

ISTQB techniques: equivalence partitioning of prefix combinations and enabled
specifications; error guessing for handler-name/rootness collisions; state
transition testing of independent inclusions and router snapshot preservation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from cosalette import App, DeviceContext, Router, Stream
from cosalette._registration import EnabledSpec
from cosalette._wiring import expand_name_specs, resolve_stream_health
from cosalette.testing import make_settings

pytestmark = pytest.mark.unit


async def feed(stream: Stream[int]) -> AsyncIterator[None]:
    """Top-level handler whose name can match an explicit stream name."""
    async for _ in stream:
        yield


@pytest.mark.parametrize("deferred", [False, True])
def test_explicit_handler_name_is_named(deferred: bool) -> None:
    """An explicit name matching the function name still permits feeds."""
    router = Router()
    enabled: EnabledSpec = (lambda _settings: True) if deferred else True
    router.stream("feed", feeds=["radon"], enabled=enabled)(feed)

    assert router._streams[0].is_root is False
    assert router._streams[0].feeds == ("radon",)


@pytest.mark.parametrize("deferred", [False, True])
def test_unnamed_nested_handler_is_root(deferred: bool) -> None:
    """Nested function qualnames cannot turn omitted names into named streams."""
    router = Router(prefix="sensors")
    enabled: EnabledSpec = (lambda _settings: True) if deferred else True

    @router.stream(enabled=enabled)
    async def nested(stream: Stream[int]) -> AsyncIterator[None]:
        async for _ in stream:
            yield

    app = App(name="bridge", version="1.0.0")
    app.include_router(router, prefix="floor1")

    assert router._streams[0].is_root is True
    assert app._streams[0].is_root is True
    with pytest.raises(ValueError, match="Root stream.*cannot declare feeds"):
        Router().stream(feeds=["radon"], enabled=enabled)(nested)


@pytest.mark.parametrize(
    ("router_prefix", "include_prefix", "expected_prefix"),
    [
        (None, None, ""),
        ("sensors", None, "sensors/"),
        (None, "floor1", "floor1/"),
        ("sensors", "floor1", "floor1/sensors/"),
    ],
)
def test_local_feeds_follow_combined_prefix(
    router_prefix: str | None,
    include_prefix: str | None,
    expected_prefix: str,
) -> None:
    """Bootstrap resolves local device/telemetry feeds and unchanged app feeds."""
    router = Router(prefix=router_prefix)

    @router.telemetry("radon", interval=30)
    async def radon() -> dict[str, object]:
        return {}

    @router.device("valve")
    async def valve(ctx: DeviceContext) -> AsyncIterator[None]:
        yield

    router.stream("feed", feeds=["radon", "valve", "external"])(feed)
    app = App(name="bridge", version="1.0.0")

    @app.telemetry("external", interval=30)
    async def external() -> dict[str, object]:
        return {}

    app.include_router(router, prefix=include_prefix)
    resolve_stream_health(app._streams, app._devices, app._telemetry, make_settings())

    assert app._streams[0].name == f"{expected_prefix}feed"
    assert app._streams[0].feeds == (
        f"{expected_prefix}radon",
        f"{expected_prefix}valve",
        "external",
    )
    assert router._streams[0].feeds == ("radon", "valve", "external")


def test_repeated_inclusions_keep_local_feed_scope() -> None:
    """Local references take precedence and each inclusion owns its targets."""
    router = Router()

    @router.telemetry("radon", interval=30)
    async def local_radon() -> dict[str, object]:
        return {}

    router.stream("feed", feeds=["radon"])(feed)
    app = App(name="bridge", version="1.0.0")

    @app.telemetry("radon", interval=30)
    async def global_radon() -> dict[str, object]:
        return {}

    app.include_router(router, prefix="room1")
    app.include_router(router, prefix="room2")
    resolve_stream_health(app._streams, app._devices, app._telemetry, make_settings())

    assert [reg.feeds for reg in app._streams] == [
        ("room1/radon",),
        ("room2/radon",),
    ]
    assert router._streams[0].feeds == ("radon",)


def test_callable_name_feeds_use_expanded_app_names() -> None:
    """Dynamic targets keep the app names produced by name-spec expansion."""
    router = Router(prefix="sensors")

    @router.telemetry(lambda _settings: ["radon"], interval=30)
    async def dynamic_radon() -> dict[str, object]:
        return {}

    router.stream("feed", feeds=["radon"])(feed)
    app = App(name="bridge", version="1.0.0")
    app.include_router(router, prefix="floor1")
    settings = make_settings()
    expand_name_specs(app._telemetry, app._devices, app._commands, settings)
    resolve_stream_health(app._streams, app._devices, app._telemetry, settings)

    assert app._streams[0].feeds == ("radon",)
    assert app._telemetry[0].name == "radon"
