use std::collections::VecDeque;

use pyo3::prelude::*;
use pyo3::types::PyBool;

use crate::validation::require_finite;

/// Sliding-window median filter — Rust drop-in for the Python implementation.
#[pyclass(module = "cosalette._filters_rs")]
pub struct MedianFilter {
    window: usize,
    buffer: VecDeque<f64>,
    value: Option<f64>,
}

#[pymethods]
impl MedianFilter {
    #[new]
    #[pyo3(signature = (window))]
    fn new(window: &Bound<'_, PyAny>) -> PyResult<Self> {
        // Reject bools before extracting (bool is a subclass of int in Python).
        if window.is_instance_of::<PyBool>() {
            let repr: String = window.repr()?.extract()?;
            return Err(pyo3::exceptions::PyTypeError::new_err(format!(
                "window must be an int, got bool: {repr}"
            )));
        }

        // Try extracting as i64 — fails for float, str, etc.
        let window_val: i64 = match window.extract() {
            Ok(v) => v,
            Err(_) => {
                let type_name: String = window
                    .get_type()
                    .qualname()
                    .and_then(|n| n.extract())
                    .unwrap_or_else(|_| "unknown".to_owned());
                let repr: String = window.repr()?.extract()?;
                return Err(pyo3::exceptions::PyTypeError::new_err(format!(
                    "window must be an int, got {type_name}: {repr}"
                )));
            }
        };

        if window_val < 1 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "window must be >= 1, got {window_val}"
            )));
        }

        // Legitimate median windows are tiny (a handful to a few hundred
        // samples). Cap generously: VecDeque::with_capacity(w) eagerly reserves
        // w * 8 bytes, so a huge window would trigger an allocator abort() that
        // bypasses pyo3's catch_unwind and kills the whole daemon. The bound
        // also keeps `as usize` exact on 32-bit targets (e.g. a 32-bit Pi).
        const MAX_WINDOW: i64 = 1 << 20; // 1_048_576 samples (~8 MiB buffer)
        if window_val > MAX_WINDOW {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "window must be <= {MAX_WINDOW}, got {window_val}"
            )));
        }

        let w = window_val as usize;
        Ok(Self {
            window: w,
            buffer: VecDeque::with_capacity(w),
            value: None,
        })
    }

    #[getter]
    fn window(&self) -> usize {
        self.window
    }

    #[getter]
    fn value(&self) -> Option<f64> {
        self.value
    }

    fn update(&mut self, raw: f64) -> PyResult<f64> {
        require_finite(raw, "raw")?;
        self.buffer.push_back(raw);
        if self.buffer.len() > self.window {
            self.buffer.pop_front();
        }

        let median = compute_median(&self.buffer);
        self.value = Some(median);
        Ok(median)
    }

    fn reset(&mut self) {
        self.buffer.clear();
        self.value = None;
    }

    fn __repr__(&self) -> String {
        let value_repr = match self.value {
            None => "None".to_owned(),
            Some(v) => format!("{v:?}"),
        };
        format!("MedianFilter(window={}, value={value_repr})", self.window)
    }
}

/// Compute the median of values in a `VecDeque`.
fn compute_median(buf: &VecDeque<f64>) -> f64 {
    let mut sorted: Vec<f64> = buf.iter().copied().collect();
    sorted.sort_by(f64::total_cmp);
    let len = sorted.len();
    if len % 2 == 1 {
        sorted[len / 2]
    } else {
        // Robust midpoint of the two middle values (sorted, so lo <= hi).
        // `lo * 0.5 + hi * 0.5` underflows for subnormals (5e-324 * 0.5 -> 0),
        // while `(lo + hi) * 0.5` overflows for opposite extremes (±1e308).
        // Branch on whether the pair straddles zero to stay safe from both:
        let lo = sorted[len / 2 - 1];
        let hi = sorted[len / 2];
        if lo <= 0.0 && hi >= 0.0 {
            // Straddles zero: magnitudes cancel, so the sum cannot overflow.
            (lo + hi) * 0.5
        } else {
            // Same sign: `hi - lo` cannot overflow, and adding half the span
            // back to `lo` never underflows the value away (0 span -> lo).
            lo + (hi - lo) * 0.5
        }
    }
}
