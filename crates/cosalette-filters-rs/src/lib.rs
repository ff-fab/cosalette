use pyo3::prelude::*;

mod median;
mod one_euro;
mod pt1;
mod validation;

/// High-performance signal filters for cosalette.
///
/// This module will provide Rust implementations of the filter protocols
/// defined in `cosalette.filters`: Pt1Filter, MedianFilter, OneEuroFilter.
#[pymodule]
fn cosalette_filters_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<median::MedianFilter>()?;
    m.add_class::<one_euro::OneEuroFilter>()?;
    m.add_class::<pt1::Pt1Filter>()?;
    Ok(())
}
