"""Shared asyncio task-management utilities for the runners package."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


async def _cancel_task(task: asyncio.Task[Any]) -> None:
    """Cancel *task* and await its completion, suppressing CancelledError."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
