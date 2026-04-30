"""Path, version, and repo-root utility helpers for the package CLI."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path


def _get_package_assets_dir() -> Path:
    """Get the path to packaged guidance assets."""
    try:
        import cosalette

        package_path = Path(cosalette.__file__).parent
        return package_path / "assets" / "guidance"
    except ImportError, AttributeError:
        # Fallback if running in development
        return Path(__file__).parent.parent / "assets" / "guidance"


def _get_version() -> str:
    """Get the cosalette package version."""
    try:
        return version("cosalette")
    except Exception:
        return "unknown"


def _find_repo_root() -> Path:
    """Walk up from cwd to find the repository root (.git marker).

    Falls back to cwd if no .git directory or file is found.
    """
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def _find_instructions_dir() -> Path:
    """Return the canonical instructions directory relative to the repo root."""
    return _find_repo_root() / ".github" / "instructions"


def _get_canonical_relative_path(target: Path) -> str:
    """Get a robust relative path to the target from the repo root.

    Falls back to absolute path if relative calculation fails.
    """
    try:
        return str(target.resolve().relative_to(_find_repo_root().resolve()))
    except ValueError:
        return str(target.resolve())


def _is_canonical_default_target(target: Path) -> bool:
    """Check if the target is the canonical default instructions file.

    Returns True only for .github/instructions/cosalette.instructions.md
    """
    try:
        target_resolved = target.resolve()
        canonical_default = (
            _find_repo_root() / ".github" / "instructions" / "cosalette.instructions.md"
        ).resolve()
        return target_resolved == canonical_default
    except OSError:
        # If path resolution fails, be conservative and return False
        return False
