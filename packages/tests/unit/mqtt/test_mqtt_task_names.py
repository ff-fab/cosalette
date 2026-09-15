"""Unit tests for asyncio task naming in MqttClient (cos-ggjl).

Verifies that all ``asyncio.create_task`` calls in ``MqttClient`` pass a
descriptive ``name=`` parameter for debuggability and GC safety.

Test Techniques Used:
    - Specification-based Testing: task names match documented convention.
    - Static Analysis: grep for unreferenced ``create_task`` calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CLIENT_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "cosalette" / "_mqtt" / "_client.py"
)


class TestCreateTaskNaming:
    """Every asyncio.create_task in _client.py must pass name=."""

    def test_all_create_task_calls_have_name_kwarg(self) -> None:
        """All asyncio.create_task() calls in _client.py pass a name= keyword."""
        source = _CLIENT_PATH.read_text()
        tree = ast.parse(source, filename=str(_CLIENT_PATH))

        unnamed: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_create_task = False
            if isinstance(func, ast.Attribute) and func.attr == "create_task":
                is_create_task = True
            if not is_create_task:
                continue
            has_name = any(kw.arg == "name" for kw in node.keywords)
            if not has_name:
                unnamed.append(node.lineno)

        assert unnamed == [], (
            f"asyncio.create_task() calls without name= at lines: {unnamed}"
        )

    def test_task_names_use_cosalette_prefix(self) -> None:
        """Task names follow the cosalette-mqtt-* convention."""
        source = _CLIENT_PATH.read_text()
        tree = ast.parse(source, filename=str(_CLIENT_PATH))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "create_task"):
                continue
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name_val = kw.value.value
                    assert isinstance(name_val, str), (
                        f"Task name at line {node.lineno} is not a string literal"
                    )
                    assert name_val.startswith("cosalette-mqtt-"), (
                        f"Task name {name_val!r} at line {node.lineno} "
                        "does not follow the cosalette-mqtt-* convention"
                    )
