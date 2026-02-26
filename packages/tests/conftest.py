"""Pytest configuration and shared fixtures."""

# The cosalette testing plugin is registered via a ``pytest11`` entry
# point (pyproject.toml) for external consumers.  In our own test
# suite we disable it (``-p no:cosalette``) and load explicitly here
# instead, because conftest-based loading is processed during
# ``pytest_load_initial_conftests`` — after ``pytest-cov`` starts
# coverage tracing — so the cosalette import chain is measured.
pytest_plugins = ["cosalette.testing._plugin"]
