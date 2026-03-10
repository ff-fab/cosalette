use std::f64::consts::PI;

use pyo3::prelude::*;
use pyo3::types::PyBool;

/// Reject bool and extract f64, matching the Python error messages exactly.
fn reject_bool(param: &Bound<'_, PyAny>, name: &str) -> PyResult<f64> {
    if param.is_instance_of::<PyBool>() {
        let repr: String = param.repr()?.extract()?;
        return Err(pyo3::exceptions::PyTypeError::new_err(format!(
            "{name} must be a number, got bool: {repr}"
        )));
    }
    param.extract()
}

/// Extract f64 from an optional PyAny, using a default when None.
fn extract_param(
    param: Option<&Bound<'_, PyAny>>,
    name: &str,
    default: f64,
) -> PyResult<f64> {
    match param {
        None => Ok(default),
        Some(p) => reject_bool(p, name),
    }
}

/// Compute smoothing factor from cutoff frequency and sample interval.
///
/// `alpha = dt / (tau + dt)` where `tau = 1 / (2 * PI * cutoff)`.
fn alpha_from_cutoff(cutoff: f64, dt: f64) -> f64 {
    let tau = 1.0 / (2.0 * PI * cutoff);
    dt / (tau + dt)
}

/// Adaptive low-pass filter (1€ Filter) — Rust drop-in for the Python implementation.
#[pyclass(module = "cosalette_filters_rs")]
pub struct OneEuroFilter {
    min_cutoff: f64,
    beta: f64,
    d_cutoff: f64,
    dt: f64,
    value: Option<f64>,
    prev_raw: Option<f64>,
    dx_filtered: f64,
}

#[pymethods]
impl OneEuroFilter {
    #[new]
    #[pyo3(
        signature = (min_cutoff=None, beta=None, d_cutoff=None, dt=None),
        text_signature = "(min_cutoff=1.0, beta=0.0, d_cutoff=1.0, dt=1.0)"
    )]
    fn new(
        min_cutoff: Option<&Bound<'_, PyAny>>,
        beta: Option<&Bound<'_, PyAny>>,
        d_cutoff: Option<&Bound<'_, PyAny>>,
        dt: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let min_cutoff_val = extract_param(min_cutoff, "min_cutoff", 1.0)?;
        let beta_val = extract_param(beta, "beta", 0.0)?;
        let d_cutoff_val = extract_param(d_cutoff, "d_cutoff", 1.0)?;
        let dt_val = extract_param(dt, "dt", 1.0)?;

        if min_cutoff_val <= 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "min_cutoff must be positive, got {:?}",
                min_cutoff_val
            )));
        }
        if beta_val < 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "beta must be non-negative, got {:?}",
                beta_val
            )));
        }
        if d_cutoff_val <= 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "d_cutoff must be positive, got {:?}",
                d_cutoff_val
            )));
        }
        if dt_val <= 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "dt must be positive, got {:?}",
                dt_val
            )));
        }

        Ok(Self {
            min_cutoff: min_cutoff_val,
            beta: beta_val,
            d_cutoff: d_cutoff_val,
            dt: dt_val,
            value: None,
            prev_raw: None,
            dx_filtered: 0.0,
        })
    }

    #[getter]
    fn min_cutoff(&self) -> f64 {
        self.min_cutoff
    }

    #[getter]
    fn beta(&self) -> f64 {
        self.beta
    }

    #[getter]
    fn d_cutoff(&self) -> f64 {
        self.d_cutoff
    }

    #[getter]
    fn dt(&self) -> f64 {
        self.dt
    }

    #[getter]
    fn value(&self) -> Option<f64> {
        self.value
    }

    fn update(&mut self, raw: f64) -> f64 {
        if self.value.is_none() {
            // First call: seed all state.
            self.value = Some(raw);
            self.prev_raw = Some(raw);
            self.dx_filtered = 0.0;
            return raw;
        }

        let prev_raw = self.prev_raw.unwrap(); // Invariant: seeded ⟹ prev_raw set
        let prev_value = self.value.unwrap();

        // 1. Raw derivative.
        let dx = (raw - prev_raw) / self.dt;

        // 2. Filter the derivative.
        let alpha_d = alpha_from_cutoff(self.d_cutoff, self.dt);
        self.dx_filtered = alpha_d * dx + (1.0 - alpha_d) * self.dx_filtered;

        // 3. Adaptive cutoff.
        let cutoff = self.min_cutoff + self.beta * self.dx_filtered.abs();

        // 4. Adaptive alpha and signal filtering.
        let alpha = alpha_from_cutoff(cutoff, self.dt);
        let new_value = alpha * raw + (1.0 - alpha) * prev_value;
        self.value = Some(new_value);

        // 5. Store previous raw.
        self.prev_raw = Some(raw);

        new_value
    }

    fn reset(&mut self) {
        self.value = None;
        self.prev_raw = None;
        self.dx_filtered = 0.0;
    }

    fn __repr__(&self) -> String {
        let value_repr = match self.value {
            None => "None".to_owned(),
            Some(v) => format!("{v:?}"),
        };
        format!(
            "OneEuroFilter(min_cutoff={:?}, beta={:?}, d_cutoff={:?}, dt={:?}, value={value_repr})",
            self.min_cutoff, self.beta, self.d_cutoff, self.dt
        )
    }
}
