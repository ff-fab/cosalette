"""Consumer-side type checks for public registration decorators.

This module is included in ``task typecheck`` but is not collected as a pytest
test. It makes sure each decorator preserves the handler's callable signature.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, assert_type

from cosalette import App

if TYPE_CHECKING:
    app = App(name="typing-example", version="0.0.0")

    @app.telemetry("telemetry", interval=1)
    async def telemetry_handler(value: int) -> dict[str, int]:
        return {"value": value}

    @app.periodic("periodic", interval=1)
    async def periodic_handler(value: int) -> None:
        _ = value

    @app.device("device")
    async def device_handler(value: int) -> AsyncGenerator[None]:
        _ = value
        yield

    @app.stream("stream")
    async def stream_handler(value: int) -> None:
        _ = value

    async def check_signatures() -> None:
        assert_type(await telemetry_handler(1), dict[str, int])
        assert_type(await periodic_handler(1), None)
        assert_type(device_handler(1), AsyncGenerator[None])
        assert_type(await stream_handler(1), None)
