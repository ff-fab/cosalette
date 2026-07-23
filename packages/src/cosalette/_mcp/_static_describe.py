"""Static, no-execution app description for the cosalette MCP server.

Unlike the introspection tools (``_introspect_tools.py``), this tool NEVER
imports or executes the target module. It resolves the module's source file on
disk and parses it with :func:`ast.parse`, extracting only statically visible
structure. It is therefore SAFE to run against untrusted modules and is
deliberately NOT subject to the ``COSALETTE_MCP_IMPORT_ALLOW`` allowlist that
gates the import-based tools.

Because nothing runs, the result is best-effort: dynamic registrations,
computed values, and definitions in other modules are not captured.

Safety invariant — this module must never import the target or evaluate its
code. It uses only filesystem lookups and the ``ast`` module (parse/unparse plus
reads of already-parsed literal nodes). It never calls ``import_module``,
``find_spec``, ``exec``, ``eval``, or ``compile`` for execution.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, TypeGuard

_STATIC_LABEL = (
    "STATIC ANALYSIS — no code executed; best-effort. Dynamic registrations, "
    "computed values, and cross-module definitions are NOT captured. Use "
    "cosalette_inspect_app for a complete (import-based) snapshot."
)


# ---------------------------------------------------------------------------
# Source resolution (no import)
# ---------------------------------------------------------------------------


def _parse_module_part(target: str) -> tuple[str | None, str | None]:
    """Return the trimmed module/file part of *target*, or an error."""
    spec = target.strip()
    module_part = spec.split(":", 1)[0].strip()
    if module_part:
        return module_part, None
    return None, "❌ Empty target. Pass a .py path or a dotted module name."


def _resolve_file_candidate(module_part: str) -> tuple[Path | None, str | None]:
    """Resolve a direct ``.py`` file path, or return ``(None, None)``."""
    candidate = Path(module_part)
    if candidate.suffix != ".py":
        return None, None
    if candidate.is_file():
        return candidate, None
    return None, f"❌ File not found: {module_part}"


def _is_valid_dotted_module(module_part: str) -> bool:
    """Return True if *module_part* is a valid dotted Python module path."""
    return all(p.isidentifier() for p in module_part.split("."))


def _iter_module_candidates(module_part: str) -> list[Path]:
    """Return source-file candidates on ``sys.path`` for *module_part*."""
    rel = Path(*module_part.split("."))
    candidates: list[Path] = []
    for entry in sys.path:
        base = Path(entry or ".")
        candidates.append(base / rel.with_suffix(".py"))
        candidates.append(base / rel / "__init__.py")
    return candidates


def _resolve_dotted_module(module_part: str) -> tuple[Path | None, str | None]:
    """Resolve a dotted module to source on disk without importing."""
    if not _is_valid_dotted_module(module_part):
        return None, (
            f"❌ '{module_part}' is neither an existing .py file nor a valid "
            "dotted module path."
        )

    for cand in _iter_module_candidates(module_part):
        if cand.is_file():
            return cand, None

    return None, (
        f"❌ Could not locate source for module '{module_part}' on sys.path "
        "without importing it. Pass a direct path to the .py file instead."
    )


def _resolve_source_path(target: str) -> tuple[Path | None, str | None]:
    """Resolve *target* to a ``.py`` source file WITHOUT importing anything.

    *target* may be a filesystem path to a ``.py`` file, or a dotted module
    path (optionally ``module:attr`` — the attribute is ignored here). Only the
    filesystem and ``sys.path`` are consulted; no module is imported.
    """
    module_part, err = _parse_module_part(target)
    if err is not None:
        return None, err
    if module_part is None:
        return None, "❌ Empty target. Pass a .py path or a dotted module name."

    file_path, file_err = _resolve_file_candidate(module_part)
    if file_path is not None or file_err is not None:
        return file_path, file_err

    return _resolve_dotted_module(module_part)


# ---------------------------------------------------------------------------
# AST extraction (no execution — ast.unparse renders source; Constant.value
# reads an already-parsed literal)
# ---------------------------------------------------------------------------


def _is_call(node: ast.expr | None) -> TypeGuard[ast.Call]:
    return isinstance(node, ast.Call)


def _describe_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    decorators = ", ".join(ast.unparse(d) for d in node.decorator_list)
    return f"{kind} {node.name}  —  @{decorators}"


def _describe_construction(node: ast.Assign | ast.AnnAssign) -> str | None:
    """Render a top-level ``name = SomeCall(...)`` construction, or ``None``."""
    if isinstance(node, ast.AnnAssign):
        target: ast.expr = node.target
        value = node.value
    else:
        if len(node.targets) != 1:
            return None
        target = node.targets[0]
        value = node.value
    if not isinstance(target, ast.Name) or not _is_call(value):
        return None
    return f"{target.id} = {ast.unparse(value)}"


def _describe_class_field(item: ast.stmt) -> str | None:
    """Render a class attribute declaration line, or ``None``."""
    if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
        return None
    annotation = ast.unparse(item.annotation)
    default = f" = {ast.unparse(item.value)}" if item.value is not None else ""
    return f"    {item.target.id}: {annotation}{default}"


def _describe_class(node: ast.ClassDef) -> list[str]:
    bases = ", ".join(ast.unparse(b) for b in node.bases)
    header = f"class {node.name}({bases})" if bases else f"class {node.name}"
    lines = [header]
    for item in node.body:
        if (field := _describe_class_field(item)) is not None:
            lines.append(field)
    return lines


def _collect_node_sections(
    node: ast.stmt,
    *,
    handlers: list[str],
    constructions: list[str],
    calls: list[str],
    classes: list[str],
) -> None:
    """Append extracted lines for one top-level AST node to output lists."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        if node.decorator_list:
            handlers.append(_describe_function(node))
        return

    if isinstance(node, ast.ClassDef):
        classes.extend(_describe_class(node))
        return

    if isinstance(node, ast.Assign | ast.AnnAssign):
        if (line := _describe_construction(node)) is not None:
            constructions.append(line)
        return

    if isinstance(node, ast.Expr) and _is_call(node.value):
        calls.append(ast.unparse(node.value))


def _extract(tree: ast.Module) -> dict[str, list[str]]:
    """Extract statically visible structure from a parsed module."""
    handlers: list[str] = []
    constructions: list[str] = []
    calls: list[str] = []
    classes: list[str] = []

    for node in tree.body:
        _collect_node_sections(
            node,
            handlers=handlers,
            constructions=constructions,
            calls=calls,
            classes=classes,
        )

    return {
        "constructions": constructions,
        "handlers": handlers,
        "calls": calls,
        "classes": classes,
    }


# ---------------------------------------------------------------------------
# Formatting + orchestration
# ---------------------------------------------------------------------------


def _format_report(path: Path, tree: ast.Module) -> str:
    """Render a static-analysis report for *tree* at *path*."""
    sections = _extract(tree)
    docstring = ast.get_docstring(tree)

    out: list[str] = [_STATIC_LABEL, "", f"Source: {path}", ""]
    out.append("## Module docstring")
    out.append(docstring.strip() if docstring else "(none)")

    titles = {
        "constructions": "Top-level constructions",
        "handlers": "Decorated handlers",
        "calls": "Top-level calls",
        "classes": "Classes",
    }
    for key, title in titles.items():
        section_lines = sections[key]
        if not section_lines:
            continue
        out.append("")
        out.append(f"## {title}")
        for line in section_lines:
            # Class field lines are pre-indented; others get a bullet.
            out.append(line if line.startswith("    ") else f"- {line}")

    return "\n".join(out)


def _describe_static(target: str) -> str:
    """Implementation of the ``cosalette_describe_app_static`` tool."""
    path, err = _resolve_source_path(target)
    if err is not None or path is None:
        return err or "❌ Could not resolve target."

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except OSError as exc:
        return f"❌ Could not read '{path}': {exc}"
    except SyntaxError as exc:
        return (
            f"{_STATIC_LABEL}\n\nSource: {path}\n\n"
            f"❌ Could not parse the file (syntax error): {exc}"
        )

    return _format_report(path, tree)


def register_static_describe_tools(mcp: Any) -> None:
    """Register the static (no-execution) describe tool with the MCP server."""

    @mcp.tool()
    def cosalette_describe_app_static(target: str) -> str:
        """Describe a cosalette module WITHOUT importing or executing it.

        Static counterpart to ``cosalette_inspect_app``: it resolves the
        module's source file and parses it with the ``ast`` module, so it runs
        NO code and is safe on untrusted modules. It is therefore NOT gated by
        ``COSALETTE_MCP_IMPORT_ALLOW``. Because nothing executes, the result is
        best-effort — dynamic or computed registrations, and definitions in
        other modules, are not captured. Use ``cosalette_inspect_app`` for a
        complete (import-based) snapshot.

        Args:
            target: Path to a ``.py`` file, or a dotted module path
                    (``myapp.main``; ``myapp.main:app`` is accepted and the
                    attribute is ignored for source resolution).

        Returns:
            A clearly-labeled best-effort description of the module's visible
            structure (docstring, top-level constructions, decorated handlers,
            top-level calls, classes).
        """
        return _describe_static(target)
