"""Schema loading and parsing for AsyncAPI 3.0.0 + x-cosalette-* extensions.

I/O module: loads AsyncAPI YAML or JSON, resolves $ref, validates extensions,
returns SchemaRegistry.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol, cast, override, runtime_checkable

from cosalette._dependency_hints import JSON_SCHEMA_ALTERNATIVE, missing_dependency
from cosalette._schema import SchemaRegistry, _extract_device_names
from cosalette._schema._loader_helpers import (
    _build_enforcement_config,
    _build_operations_from_raw,
    _extract_channels,
    _extract_operations_raw,
    _extract_topic_prefix,
    _infer_channel_directions,
    _validate_extensions,
    find_unreachable_consumer_channels,
)


@dataclass
class SchemaLoadError(Exception):
    """Raised when an AsyncAPI document cannot be loaded."""

    errors: list[str]
    source_description: str

    @override
    def __str__(self) -> str:
        header = f"Failed to load schema from {self.source_description}"
        if len(self.errors) == 1:
            return f"{header}: {self.errors[0]}"
        bullet_list = "\n".join(f"  - {e}" for e in self.errors)
        return f"{header} ({len(self.errors)} errors):\n{bullet_list}"


@runtime_checkable
class SchemaSource(Protocol):
    """Source for schema content."""

    async def load(self) -> str: ...

    @property
    def description(self) -> str: ...


@dataclass(frozen=True)
class FileSchemaSource:
    """Schema source from file."""

    path: Path

    async def load(self) -> str:
        def _read() -> str:
            return self.path.read_text(encoding="utf-8")

        return await asyncio.to_thread(_read)

    @property
    def description(self) -> str:
        return f"file://{self.path}"


@dataclass(frozen=True)
class InlineSchemaSource:
    """Schema source from inline content."""

    content: str

    async def load(self) -> str:
        return self.content

    @property
    def description(self) -> str:
        return "<inline>"


def require_optional(module: str, feature: str, alternative: str | None = None) -> None:
    """Raise ImportError with a dependency hint when *module* is not installed.

    Checks presence only, without importing: importing jsonschema or yaml
    would keep about 70 modules resident.  Each is imported where it is used.
    The message comes from :func:`cosalette._dependency_hints.missing_dependency`.
    """
    if find_spec(module) is None:
        message, hint = missing_dependency(module, feature, alternative)
        raise ImportError(f"{message}\n\n{hint}")


def _is_json_source(source: SchemaSource) -> bool:
    """A file source named ``*.json`` is parsed as JSON, not YAML."""
    return (
        isinstance(source, FileSchemaSource) and source.path.suffix.lower() == ".json"
    )


def _follow_pointer(root: dict[str, Any], pointer: str) -> Any:
    """Navigate JSON Pointer like #/components/schemas/Foo."""
    if not pointer.startswith("#/"):
        raise ValueError(f"Invalid pointer: {pointer}")

    path = pointer[2:]  # Remove "#/"
    parts = path.split("/") if path else []

    current = root
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Pointer {pointer} not found")
        current = current[part]

    return current


_MAX_REF_DEPTH = 50


def _resolve_refs(
    doc: dict[str, Any],
    root: dict[str, Any],
    visited: frozenset[str] = frozenset(),
    *,
    _depth: int = 0,
) -> dict[str, Any]:
    """Resolve internal $ref recursively with circular detection."""
    if _depth > _MAX_REF_DEPTH:
        raise ValueError(f"Maximum $ref nesting depth ({_MAX_REF_DEPTH}) exceeded")

    if "$ref" in doc:
        ref = doc["$ref"]
        if ref in visited:
            raise ValueError(f"Circular reference detected: {ref}")

        try:
            resolved = _follow_pointer(root, ref)
            return _resolve_refs(resolved, root, visited | {ref}, _depth=_depth + 1)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Cannot resolve $ref {ref}: {exc}") from exc

    if isinstance(doc, dict):
        return {
            key: _resolve_refs(value, root, visited, _depth=_depth + 1)
            if isinstance(value, dict)
            else value
            for key, value in doc.items()
        }

    return doc


async def _parse_text_source(source: SchemaSource) -> dict[str, Any]:
    """Parse *source* as JSON (``*.json`` file) or YAML (anything else)."""
    if _is_json_source(source):
        import orjson

        fmt, parse = "JSON", orjson.loads
    else:
        require_optional("yaml", "A YAML schema", JSON_SCHEMA_ALTERNATIVE)
        import yaml

        fmt, parse = "YAML", yaml.safe_load
    try:
        doc = parse(await source.load())
    except Exception as exc:
        raise SchemaLoadError(
            errors=[f"Failed to parse {fmt}: {exc}"],
            source_description=source.description,
        ) from exc
    if not isinstance(doc, dict):
        raise SchemaLoadError(
            errors=[f"Schema must be a {fmt} mapping, got: {type(doc).__name__}"],
            source_description=source.description,
        )
    return cast("dict[str, Any]", doc)


async def load_schema(source: SchemaSource | dict[str, Any]) -> SchemaRegistry:
    """Load and parse AsyncAPI schema from source.

    A dict source (runtime discovery, ADR-059) and a ``*.json`` file are
    parsed without PyYAML; any other text source requires it.  jsonschema is
    never needed here: only publish-time validation uses it.
    """
    if isinstance(source, dict):
        doc: dict[str, Any] = cast("dict[str, Any]", source)
        source_description = "<dict>"
    else:
        doc = await _parse_text_source(source)
        source_description = source.description

    errors = []

    # Validate AsyncAPI version
    asyncapi_version = doc.get("asyncapi", "")
    if not asyncapi_version.startswith("3.0."):
        errors.append(
            f"Unsupported AsyncAPI version: {asyncapi_version}. Expected 3.0.x"
        )

    # Validate extensions BEFORE resolving refs
    extension_errors = _validate_extensions(doc)
    errors.extend(extension_errors)

    if errors:
        raise SchemaLoadError(errors=errors, source_description=source_description)

    # Extract operations BEFORE resolving refs (so $ref is still intact)
    operations_raw = _extract_operations_raw(doc)

    try:
        # Resolve $ref (internal only)
        doc = _resolve_refs(doc, doc)
    except ValueError as exc:
        errors.append(str(exc))
        raise SchemaLoadError(
            errors=errors,
            source_description=source_description,
        ) from exc

    # Extract enforcement config
    enforcement_raw = doc.get("x-cosalette-enforcement", {})
    enforcement = _build_enforcement_config(enforcement_raw)

    # Extract channels AFTER resolving refs
    channels = _extract_channels(doc)

    # Build operations using the raw refs and resolved channels
    operations = _build_operations_from_raw(operations_raw, channels)

    # Infer channel directions from operations
    channels = _infer_channel_directions(channels, operations)

    # Extract component schemas
    component_schemas: dict[str, dict[str, Any]] = doc.get("components", {}).get(
        "schemas", {}
    )

    # Extract the resolved topic prefix (ADR-072) before device names: it
    # determines how many leading address segments the prefix occupies.
    topic_prefix = _extract_topic_prefix(doc)

    # Extract device names
    device_names = _extract_device_names(channels, topic_prefix)

    # Determine app_name
    app_name = doc.get("info", {}).get("title")
    if enforcement.network_level:
        app_name = None

    # Diagnostic (F23): flag channels where a x-cosalette-consumer block exists
    # somewhere in the payload schema but sits beyond the loader's one-level
    # descent, so it never becomes a PropertySchema.consumer.
    unreachable_consumer_channels = find_unreachable_consumer_channels(channels)

    return SchemaRegistry(
        app_name=app_name,
        app_version=doc.get("info", {}).get("version", "0.0.0"),
        asyncapi_version=asyncapi_version,
        enforcement=enforcement,
        channels=channels,
        operations=operations,
        component_schemas=component_schemas,
        device_names=device_names,
        unreachable_consumer_channels=unreachable_consumer_channels,
        topic_prefix=topic_prefix,
    )


def load_schema_sync(source: SchemaSource | dict[str, Any]) -> SchemaRegistry:
    """Synchronous wrapper around :func:`load_schema` for CLI contexts.

    Intended for CLI commands (``cosalette schema …``) where no event
    loop is running.  Calls :func:`asyncio.run` internally — do **not**
    call from within an existing async context.

    Raises:
        SchemaLoadError: When the schema document is invalid.
        ImportError: When PyYAML is missing for a YAML schema.
        RuntimeError: When called from within a running event loop.
    """
    return asyncio.run(load_schema(source))
