"""Configuration schema tools for the cosalette MCP server.

Provides tools for inspecting cosalette configuration schema and
environment variables.

Security: These tools accept user-provided ``module:attribute`` specs and
import them dynamically.  See ``_imports.py`` for risk discussion.

Secret redaction: Defaults for fields whose JSON-schema ``format`` is
``"password"`` (Pydantic ``SecretStr``/``SecretBytes``) or whose name
matches common secret patterns are replaced with ``"<redacted>"``.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

# Cache: normalised spec → schema dict
_schema_cache: dict[str, dict[str, Any]] = {}

_SECRET_NAME_RE = re.compile(
    r"(password|secret|token|api[_-]?key|private[_-]?key)", re.IGNORECASE
)


def _is_sensitive(field_name: str, field_schema: dict[str, Any]) -> bool:
    """Return True if a field looks like it holds a secret value."""
    if field_schema.get("format") == "password":
        return True
    return bool(_SECRET_NAME_RE.search(field_name))


def _redact_schema_defaults(schema: dict[str, Any]) -> dict[str, Any]:
    """Redact defaults of secret-looking fields in a JSON schema (in place).

    Mirrors the env-var redaction (``_resolve_default``) for the raw-schema
    tool: any field whose name or ``format`` marks it as a secret has its
    ``default`` replaced with ``"<redacted>"``.  Covers the top-level
    properties and every ``$defs`` submodel so hard-coded secret defaults in
    a developer's Settings source are never surfaced verbatim.
    """
    defs = schema.get("$defs", {})
    blocks = [schema.get("properties")]
    blocks += [d.get("properties") for d in defs.values() if isinstance(d, dict)]
    for props in blocks:
        if not isinstance(props, dict):
            continue
        for field_name, field_schema in props.items():
            if (
                isinstance(field_schema, dict)
                and "default" in field_schema
                and _is_sensitive(field_name, field_schema)
            ):
                field_schema["default"] = "<redacted>"
    return schema


def _import_settings(spec: str) -> tuple[Any, str | None]:
    """Import and validate a BaseSettings class from spec.

    An empty *spec* returns the base ``cosalette.Settings``.

    Returns:
        ``(settings_class, None)`` on success, ``(None, error)`` on failure.
    """
    if not spec.strip():
        from cosalette._settings import Settings

        return Settings, None

    from cosalette._mcp._imports import import_from_spec

    obj, err = import_from_spec(spec)
    if err is not None:
        return None, err

    from pydantic_settings import BaseSettings

    if not (isinstance(obj, type) and issubclass(obj, BaseSettings)):
        actual_type = type(obj).__name__
        return None, f"❌ '{spec}' is not a BaseSettings class (found {actual_type})"

    return obj, None


def _get_or_generate_schema(spec: str, settings_class: Any) -> dict[str, Any]:
    """Return a cached JSON schema for *settings_class*."""
    key = spec or "_base_"
    if key not in _schema_cache:
        _schema_cache[key] = settings_class.model_json_schema()
    return _schema_cache[key]


def _read_delimiter(settings_class: Any) -> str:
    """Read ``env_nested_delimiter`` from *settings_class*, defaulting to ``__``."""
    if hasattr(settings_class, "model_config"):
        config = settings_class.model_config
        if hasattr(config, "get"):
            return config.get("env_nested_delimiter", "__") or "__"
        if hasattr(config, "env_nested_delimiter"):
            return config.env_nested_delimiter or "__"
    return "__"


def _read_env_prefix(settings_class: Any) -> str:
    """Read ``env_prefix`` from *settings_class*, defaulting to ``""``."""
    if hasattr(settings_class, "model_config"):
        config = settings_class.model_config
        if hasattr(config, "get"):
            return str(config.get("env_prefix", "") or "")
        if hasattr(config, "env_prefix"):
            return str(config.env_prefix or "")
    return ""


def _config_schema_impl(settings_spec: str) -> str:
    """Implementation of ``cosalette_config_schema`` tool."""
    settings_class, err = _import_settings(settings_spec)
    if err is not None:
        return err

    try:
        schema = _get_or_generate_schema(settings_spec, settings_class)
        redacted = _redact_schema_defaults(deepcopy(schema))
        import json

        return json.dumps(redacted, indent=2)
    except Exception as e:
        return f"❌ Error generating schema: {e}"


def _config_env_vars_impl(settings_spec: str) -> str:
    """Implementation of ``cosalette_config_env_vars`` tool."""
    settings_class, err = _import_settings(settings_spec)
    if err is not None:
        return err

    try:
        schema = _get_or_generate_schema(settings_spec, settings_class)
        env_prefix = _read_env_prefix(settings_class)
        delimiter = _read_delimiter(settings_class)

        env_vars: list[tuple[str, str, str, str]] = []
        _collect_env_vars(
            schema,
            schema.get("properties", {}),
            env_prefix,
            "",
            delimiter,
            env_vars,
        )

        if not env_vars:
            return "No environment variables found"

        result = ["Environment variables:"]
        for var_name, description, var_type, default in env_vars:
            default_str = f", default: {default}" if default != "<required>" else ""
            result.append(f"  {var_name}: {description} ({var_type}{default_str})")

        return "\n".join(result)
    except Exception as e:
        return f"❌ Error generating environment variables: {e}"


def register_config_tools(mcp: Any) -> None:
    """Register configuration tools with the MCP server."""

    @mcp.tool()
    def cosalette_config_schema(settings_spec: str = "") -> str:
        """Get the JSON schema for cosalette configuration settings.

        Imports the module specified by *settings_spec*, which executes the
        module's top-level code; imports are gated by the
        COSALETTE_MCP_IMPORT_ALLOW allowlist (see the _imports security note).
        Defaults for secret fields are redacted.

        Args:
            settings_spec: Optional settings spec as
                ``module.path:attribute``. Empty = base Settings.

        Returns:
            JSON schema as a formatted string
        """
        return _config_schema_impl(settings_spec)

    @mcp.tool()
    def cosalette_config_env_vars(settings_spec: str = "") -> str:
        """Get environment variable names and descriptions for cosalette configuration.

        Imports the module specified by *settings_spec*, which executes the
        module's top-level code; imports are gated by the
        COSALETTE_MCP_IMPORT_ALLOW allowlist (see the _imports security note).
        Defaults for secret fields are redacted.

        Args:
            settings_spec: Optional settings spec as
                ``module.path:attribute``. Empty = base Settings.

        Returns:
            Formatted list of environment variables with types and defaults
        """
        return _config_env_vars_impl(settings_spec)


# ---------------------------------------------------------------------------
# Schema traversal helpers
# ---------------------------------------------------------------------------


def _collect_env_vars(
    schema: dict[str, Any],
    properties: dict[str, Any],
    prefix: str,
    path: str,
    delimiter: str,
    result: list[tuple[str, str, str, str]],
) -> None:
    """Walk JSON-schema *properties* and collect ``(env_name, desc, type, default)``."""
    for field_name, field_schema in properties.items():
        _process_field(
            schema,
            field_name,
            field_schema,
            prefix,
            path,
            delimiter,
            result,
        )


def _process_field(
    schema: dict[str, Any],
    field_name: str,
    field_schema: dict[str, Any],
    prefix: str,
    path: str,
    delimiter: str,
    result: list[tuple[str, str, str, str]],
) -> None:
    """Process a single JSON-schema property into env-var entries."""
    env_name = _build_env_name(prefix, path, delimiter, field_name)
    description = field_schema.get("description", field_name)
    field_type = field_schema.get("type", "unknown")
    default = _resolve_default(field_name, field_schema)

    if "$ref" in field_schema:
        _handle_ref(
            schema,
            field_schema,
            schema.get("$defs", {}),
            env_name,
            description,
            prefix,
            path,
            field_name,
            delimiter,
            result,
        )
    elif field_type == "object" and "properties" in field_schema:
        nested = f"{path}{delimiter}{field_name}" if path else field_name
        _collect_env_vars(
            schema,
            field_schema["properties"],
            prefix,
            nested,
            delimiter,
            result,
        )
    else:
        if default not in ("<required>", "<redacted>"):
            default = str(default)
        result.append((env_name, description, field_type, default))


def _build_env_name(prefix: str, path: str, delimiter: str, field_name: str) -> str:
    """Build an uppercase environment variable name."""
    if path:
        return f"{prefix}{path}{delimiter}{field_name}".upper()
    return f"{prefix}{field_name}".upper()


def _resolve_default(field_name: str, field_schema: dict[str, Any]) -> str:
    """Return the default value for a field, redacting secrets."""
    default = field_schema.get("default", "<required>")
    if default != "<required>" and _is_sensitive(field_name, field_schema):
        return "<redacted>"
    return str(default)


def _handle_ref(
    schema: dict[str, Any],
    field_schema: dict[str, Any],
    defs: dict[str, Any],
    env_name: str,
    description: str,
    prefix: str,
    path: str,
    field_name: str,
    delimiter: str,
    result: list[tuple[str, str, str, str]],
) -> None:
    """Resolve a single ``$ref`` in a field schema."""
    ref_path = field_schema["$ref"]
    if not ref_path.startswith("#/$defs/"):
        result.append((env_name, description, "unknown", "<external-ref>"))
        return

    ref_name = ref_path[len("#/$defs/") :]
    ref_schema = defs.get(ref_name)
    if ref_schema is None:
        result.append((env_name, description, "unknown", "<ref-not-found>"))
        return

    if "properties" in ref_schema:
        nested = f"{path}{delimiter}{field_name}" if path else field_name
        _collect_env_vars(
            schema,
            ref_schema["properties"],
            prefix,
            nested,
            delimiter,
            result,
        )
    else:
        result.append(
            (env_name, description, ref_schema.get("type", "object"), "<nested>")
        )
