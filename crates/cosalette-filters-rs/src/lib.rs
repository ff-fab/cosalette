use pyo3::prelude::*;

/// High-performance signal filters for cosalette.
///
/// This module will provide Rust implementations of the filter protocols
/// defined in `cosalette.filters`: Pt1Filter, MedianFilter, OneEuroFilter.
#[pymodule]
fn cosalette_filters_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
