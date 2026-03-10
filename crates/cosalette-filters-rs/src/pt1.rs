use pyo3::prelude::*;
use pyo3::types::PyBool;

use crate::validation::require_finite;

/// First-order low-pass (PT1) filter — Rust drop-in for the Python implementation.
#[pyclass(module = "cosalette._filters_rs")]
pub struct Pt1Filter {
    tau: f64,
    dt: f64,
    alpha: f64,
    value: Option<f64>,
}

#[pymethods]
impl Pt1Filter {
    #[new]
    fn new(tau: &Bound<'_, PyAny>, dt: &Bound<'_, PyAny>) -> PyResult<Self> {
        // Reject bools before extracting as f64 (pyo3 coerces bool → int → f64).
        if tau.is_instance_of::<PyBool>() {
            let repr: String = tau.repr()?.extract()?;
            return Err(pyo3::exceptions::PyTypeError::new_err(format!(
                "tau must be a number, got bool: {repr}"
            )));
        }
        if dt.is_instance_of::<PyBool>() {
            let repr: String = dt.repr()?.extract()?;
            return Err(pyo3::exceptions::PyTypeError::new_err(format!(
                "dt must be a number, got bool: {repr}"
            )));
        }

        let tau_val: f64 = tau.extract()?;
        let dt_val: f64 = dt.extract()?;

        require_finite(tau_val, "tau")?;
        require_finite(dt_val, "dt")?;

        if tau_val <= 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "tau must be positive, got {:?}",
                tau_val
            )));
        }
        if dt_val <= 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "dt must be positive, got {:?}",
                dt_val
            )));
        }

        let alpha = dt_val / (tau_val + dt_val);
        Ok(Self {
            tau: tau_val,
            dt: dt_val,
            alpha,
            value: None,
        })
    }

    #[getter]
    fn tau(&self) -> f64 {
        self.tau
    }

    #[getter]
    fn dt(&self) -> f64 {
        self.dt
    }

    #[getter]
    fn alpha(&self) -> f64 {
        self.alpha
    }

    #[getter]
    fn value(&self) -> Option<f64> {
        self.value
    }

    fn update(&mut self, raw: f64) -> PyResult<f64> {
        require_finite(raw, "raw")?;
        let v = match self.value {
            None => raw,
            Some(prev) => self.alpha * raw + (1.0 - self.alpha) * prev,
        };
        self.value = Some(v);
        Ok(v)
    }

    fn reset(&mut self) {
        self.value = None;
    }

    fn __repr__(&self) -> String {
        let value_repr = match self.value {
            None => "None".to_owned(),
            Some(v) => format!("{v:?}"),
        };
        format!(
            "Pt1Filter(tau={:?}, dt={:?}, value={value_repr})",
            self.tau, self.dt
        )
    }
}
