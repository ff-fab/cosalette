"""Configuration schema tools for the cosalette MCP server.

Provides tools for inspecting cosalette configuration schema and
environment variables.
"""

from __future__ import annotations

import importlib
from typing import Any


def register_config_tools(mcp: Any) -> None:
    """Register configuration tools with the MCP server."""

    @mcp.tool()
    def cosalette_config_schema(settings_spec: str = "") -> str:
        """Get the JSON schema for cosalette configuration settings.

        Args:
            settings_spec: Optional settings spec as
                ``module.path:attribute``. Empty = base Settings.

        Returns:
            JSON schema as a formatted string
        """
        settings_class = _import_settings_class(settings_spec)
        if isinstance(settings_class, str):
            return settings_class  # Error message

        try:
            schema = settings_class.model_json_schema()
            import json

            return json.dumps(schema, indent=2)
        except Exception as e:
            return f"❌ Error generating schema: {e}"

    @mcp.tool()
    def cosalette_config_env_vars(settings_spec: str = "") -> str:
        """Get environment variable names and descriptions for cosalette configuration.

        Args:
            settings_spec: Optional settings spec as
                ``module.path:attribute``. Empty = base Settings.

        Returns:
            Formatted list of environment variables with types and defaults
        """
        settings_class = _import_settings_class(settings_spec)
        if isinstance(settings_class, str):
            return settings_class  # Error message

        try:
            schema = settings_class.model_json_schema()

            # Get env prefix from model config
            env_prefix = ""
            if hasattr(settings_class, "model_config"):
                config = settings_class.model_config
                if hasattr(config, "get"):
                    env_prefix = config.get("env_prefix", "")
                elif hasattr(config, "env_prefix"):
                    env_prefix = config.env_prefix or ""

            env_vars: list[tuple[str, str, str, str]] = []
            _resolve_schema_references(
                schema, schema.get("properties", {}), env_prefix, "", env_vars
            )

            if not env_vars:
                return "No environment variables found"

            # Format the results
            result = ["Environment variables:"]
            for var_name, description, var_type, default in env_vars:
                default_str = f", default: {default}" if default != "<required>" else ""
                result.append(f"  {var_name}: {description} ({var_type}{default_str})")

            return "\n".join(result)

        except Exception as e:
            return f"❌ Error generating environment variables: {e}"


def _import_settings_class(spec: str) -> Any:
    """Import Settings class from module:attribute specification.

    Args:
        spec: Import specification in format "module.path:attribute".
              Empty string means use base cosalette Settings.

    Returns:
        Settings class on success, error message string on failure
    """
    if not spec.strip():
        # Use base cosalette Settings
        from cosalette._settings import Settings

        return Settings

    spec = spec.strip()
    if ":" not in spec:
        return (
            f"❌ Invalid settings spec '{spec}'. "
            "Expected format: 'module.path:attribute'"
        )

    try:
        module_path, attr_name = spec.rsplit(":", 1)
        module_path = module_path.strip()
        attr_name = attr_name.strip()

        # Import the module
        module = importlib.import_module(module_path)

        # Get the attribute
        if not hasattr(module, attr_name):
            return f"❌ Module '{module_path}' has no attribute '{attr_name}'"

        settings_class = getattr(module, attr_name)

        # Validate it's a BaseSettings subclass
        from pydantic_settings import BaseSettings

        if not (
            isinstance(settings_class, type)
            and issubclass(settings_class, BaseSettings)
        ):
            actual_type = type(settings_class).__name__
            return f"❌ '{spec}' is not a BaseSettings class (found {actual_type})"

        return settings_class

    except ImportError as e:
        return f"❌ Could not import module '{module_path}': {e}"
    except Exception as e:
        return f"❌ Error importing '{spec}': {e}"


def _resolve_schema_references(
    schema: dict[str, Any],
    properties: dict[str, Any],
    prefix: str,
    path: str,
    result: list[tuple[str, str, str, str]],
) -> None:
    """Resolve $ref references in schema properties and collect env vars.

    Args:
        schema: Full JSON schema with $defs
        properties: Properties dict to process
        prefix: Environment variable prefix
        path: Current path for nested properties
        result: List to append (var_name, description, type, default) tuples
    """
    for field_name, field_schema in properties.items():
        # Build environment variable name
        if path:
            env_name = f"{prefix}{path}__{field_name}".upper()
        else:
            env_name = f"{prefix}{field_name}".upper()

        # Get field info
        description = field_schema.get("description", field_name)
        field_type = field_schema.get("type", "unknown")
        default = field_schema.get("default", "<required>")

        # Handle $ref references
        if "$ref" in field_schema:
            ref_path = field_schema["$ref"]
            if ref_path.startswith("#/$defs/"):
                # Extract reference name and look it up in $defs
                ref_name = ref_path[8:]  # Remove "#/$defs/"
                defs = schema.get("$defs", {})
                if ref_name in defs:
                    ref_schema = defs[ref_name]
                    if "properties" in ref_schema:
                        # Recursively process the referenced schema properties
                        nested_path = f"{path}__{field_name}" if path else field_name
                        _resolve_schema_references(
                            schema,
                            ref_schema["properties"],
                            prefix,
                            nested_path,
                            result,
                        )
                    else:
                        # Record as single env var if no nested properties
                        result.append(
                            (
                                env_name,
                                description,
                                ref_schema.get("type", "object"),
                                "<nested>",
                            )
                        )
                else:
                    # Unknown reference
                    result.append((env_name, description, "unknown", "<ref-not-found>"))
            else:
                # Non-local reference
                result.append((env_name, description, "unknown", "<external-ref>"))
        # Handle nested objects
        elif field_type == "object" and "properties" in field_schema:
            nested_path = f"{path}__{field_name}" if path else field_name
            _resolve_schema_references(
                schema, field_schema["properties"], prefix, nested_path, result
            )
        else:
            # Convert default to string representation
            if default != "<required>":
                default = str(default)

            result.append((env_name, description, field_type, default))
