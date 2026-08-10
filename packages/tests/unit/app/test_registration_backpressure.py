"""Tests for command/device registration maxsize and backpressure (Finding 3a)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from cosalette import App, Router


def test_command_registration_stores_maxsize_backpressure() -> None:
    """@app.command stores maxsize and backpressure on registration."""
    app = App(name="test")

    @app.command("test", maxsize=10, backpressure="drop_oldest")
    async def handler(topic: str, payload: str) -> None:
        pass

    assert len(app._commands) == 1
    reg = app._commands[0]
    assert reg.maxsize == 10
    assert reg.backpressure == "drop_oldest"


def test_command_registration_defaults() -> None:
    """@app.command defaults to unbounded (maxsize=0) and drop_newest."""
    app = App(name="test")

    @app.command("test")
    async def handler(topic: str, payload: str) -> None:
        pass

    assert len(app._commands) == 1
    reg = app._commands[0]
    assert reg.maxsize == 0
    assert reg.backpressure == "drop_newest"


def test_device_registration_stores_maxsize_backpressure() -> None:
    """@app.device stores maxsize and backpressure on registration."""
    app = App(name="test")

    @app.device("test", maxsize=5, backpressure="raise")
    async def handler() -> AsyncGenerator[None]:
        yield

    assert len(app._devices) == 1
    reg = app._devices[0]
    assert reg.maxsize == 5
    assert reg.backpressure == "raise"


def test_device_registration_defaults() -> None:
    """@app.device defaults to unbounded (maxsize=0) and drop_newest."""
    app = App(name="test")

    @app.device("test")
    async def handler() -> AsyncGenerator[None]:
        yield

    assert len(app._devices) == 1
    reg = app._devices[0]
    assert reg.maxsize == 0
    assert reg.backpressure == "drop_newest"


def test_router_command_registration_stores_maxsize_backpressure() -> None:
    """@router.command stores maxsize and backpressure on registration."""
    router = Router()

    @router.command("test", maxsize=20, backpressure="drop_oldest")
    async def handler(topic: str, payload: str) -> None:
        pass

    assert len(router._commands) == 1
    reg = router._commands[0]
    assert reg.maxsize == 20
    assert reg.backpressure == "drop_oldest"


def test_router_device_registration_stores_maxsize_backpressure() -> None:
    """@router.device stores maxsize and backpressure on registration."""
    router = Router()

    @router.device("test", maxsize=15, backpressure="raise")
    async def handler() -> AsyncGenerator[None]:
        yield

    assert len(router._devices) == 1
    reg = router._devices[0]
    assert reg.maxsize == 15
    assert reg.backpressure == "raise"


def test_include_router_preserves_maxsize_backpressure() -> None:
    """include_router preserves maxsize and backpressure from router registrations."""
    app = App(name="test")
    router = Router()

    @router.command("cmd", maxsize=10, backpressure="drop_oldest")
    async def cmd_handler(topic: str, payload: str) -> None:
        pass

    @router.device("dev", maxsize=5, backpressure="raise")
    async def dev_handler() -> AsyncGenerator[None]:
        yield

    app.include_router(router)

    # Command registration preserved
    cmd_reg = next(r for r in app._commands if r.name == "cmd")
    assert cmd_reg.maxsize == 10
    assert cmd_reg.backpressure == "drop_oldest"

    # Device registration preserved
    dev_reg = next(r for r in app._devices if r.name == "dev")
    assert dev_reg.maxsize == 5
    assert dev_reg.backpressure == "raise"


def test_add_command_imperative_stores_maxsize_backpressure() -> None:
    """app.add_command stores maxsize and backpressure."""
    app = App(name="test")

    async def handler(topic: str, payload: str) -> None:
        pass

    app.add_command("test", handler, maxsize=8, backpressure="drop_oldest")

    assert len(app._commands) == 1
    reg = app._commands[0]
    assert reg.maxsize == 8
    assert reg.backpressure == "drop_oldest"


def test_add_device_imperative_stores_maxsize_backpressure() -> None:
    """app.add_device stores maxsize and backpressure."""
    app = App(name="test")

    async def handler() -> AsyncGenerator[None]:
        yield

    app.add_device("test", handler, maxsize=12, backpressure="raise")

    assert len(app._devices) == 1
    reg = app._devices[0]
    assert reg.maxsize == 12
    assert reg.backpressure == "raise"
