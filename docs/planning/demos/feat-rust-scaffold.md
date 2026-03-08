# Rust Project Scaffold (pyo3/maturin)

*2026-03-08T18:55:47Z by Showboat 0.6.1*
<!-- showboat-id: c81f6abe-8d5a-4426-a08a-2749ffc44d6e -->

Created cosalette-filters-rs/ scaffold: Cargo.toml (pyo3 0.28, cdylib), pyproject.toml (maturin build backend), src/lib.rs (empty #[pymodule] with __version__). This is the foundation for COS-82m — subsequent tasks will add Pt1Filter, MedianFilter, and OneEuroFilter as #[pyclass] implementations.

```bash
VIRTUAL_ENV=/workspace/packages/.venv uv run python -c "import cosalette_filters_rs; print(f'Module loaded: {cosalette_filters_rs.__name__}'); print(f'Version: {cosalette_filters_rs.__version__}')"
```

```output
Module loaded: cosalette_filters_rs
Version: 0.1.0
```
