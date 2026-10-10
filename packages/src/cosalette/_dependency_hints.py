"""Installer-neutral messages for a missing optional dependency (ADR-033).

Every runtime and CLI message about a missing optional dependency is built
here, so the wording cannot drift between call sites.  A message names the
missing package first, then every cosalette extra that contains it, then
the alternative that avoids the dependency where one exists.  It names no
installer: slim images often have neither pip nor uv.
"""

from __future__ import annotations

# Import name -> (PyPI package, cosalette extras containing it), mirroring
# [project.optional-dependencies] in pyproject.toml.
OPTIONAL_DEPENDENCIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "yaml": ("pyyaml", ("schema", "config-yaml")),
    "jsonschema": ("jsonschema", ("schema",)),
}

JSON_SCHEMA_ALTERNATIVE = "use a .json schema (cosalette schema dump --format json)"


def missing_dependency(
    module: str, feature: str, alternative: str | None = None
) -> tuple[str, str]:
    """Return ``(message, hint)`` for *feature* failing on a missing *module*.

    Args:
        module: Import name of the missing dependency (``yaml``, ``jsonschema``).
        feature: What needs it, as a sentence subject (``A YAML schema``).
        alternative: How to avoid the dependency, if there is a way.

    Returns:
        ``message`` such as ``A YAML schema requires pyyaml, which is not
        installed.`` and a ``hint`` starting with ``Hint:``.
    """
    package, extras = OPTIONAL_DEPENDENCIES[module]
    names = " or ".join(f"cosalette[{extra}]" for extra in extras)
    hint = f"Hint: add {package} (or the {names} extra) to your project dependencies"
    if alternative:
        hint += f", or {alternative}"
    return f"{feature} requires {package}, which is not installed.", f"{hint}."
