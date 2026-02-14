#!/usr/bin/env python
"""Script to regenerate version file from git tags using setuptools_scm."""

import sys
from pathlib import Path

try:
    import setuptools_scm
except ImportError:
    print("setuptools_scm not found. Install with: uv sync --group dev")
    sys.exit(1)

# Use absolute path for workspace root
workspace_root = Path("/workspace")
version = setuptools_scm.get_version(root=workspace_root, fallback_version="0.0.0")
version_file = workspace_root / "packages" / "src" / "cosalette" / "_version.py"
version_file.write_text(f'__version__ = "{version}"\n')
print(f"Updated {version_file} with version: {version}")
