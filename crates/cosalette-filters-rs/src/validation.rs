use pyo3::prelude::*;

/// Reject non-finite f64 values (NaN, +Inf, -Inf).
///
/// Call after extracting a Python argument and before any range checks.
pub fn require_finite(val: f64, name: &str) -> PyResult<()> {
    if !val.is_finite() {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "{name} must be finite, got {val:?}"
        )));
    }
    Ok(())
}
