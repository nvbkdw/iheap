//! Python bindings for [`iheap_core::IndexedHeap`].
//!
//! Only `f64` priorities and integer handles cross the FFI boundary; any data
//! associated with a handle is owned by the Python caller.

use iheap_core::{Error, Handle, IndexedHeap as CoreHeap, Order};
use pyo3::exceptions::{PyIndexError, PyKeyError, PyOverflowError, PyValueError};
use pyo3::prelude::*;

fn to_py_err(err: Error) -> PyErr {
    match err {
        Error::InvalidHandle(h) => PyKeyError::new_err(h),
        Error::NanPriority | Error::LengthMismatch { .. } => PyValueError::new_err(err.to_string()),
        Error::CapacityOverflow => PyOverflowError::new_err(err.to_string()),
    }
}

/// Indexed priority queue of float priorities addressed by integer handles.
///
/// ``push`` returns a handle that stays valid until the entry is popped or
/// removed. Handles are small non-negative integers below ``handle_bound``
/// and are recycled, so callers can keep per-entry data in a list indexed by
/// handle.
#[pyclass(module = "iheap._iheap", name = "IndexedHeap")]
#[derive(Clone)]
struct PyIndexedHeap {
    inner: CoreHeap,
}

#[pymethods]
impl PyIndexedHeap {
    #[new]
    #[pyo3(signature = (*, reverse = false, capacity = 0))]
    fn new(reverse: bool, capacity: usize) -> Self {
        let order = if reverse { Order::Max } else { Order::Min };
        Self {
            inner: CoreHeap::with_capacity(order, capacity),
        }
    }

    /// True for a max-heap.
    #[getter]
    fn reverse(&self) -> bool {
        self.inner.order() == Order::Max
    }

    /// Every live handle is strictly below this value.
    #[getter]
    fn handle_bound(&self) -> usize {
        self.inner.handle_bound()
    }

    fn __len__(&self) -> usize {
        self.inner.len()
    }

    fn __bool__(&self) -> bool {
        !self.inner.is_empty()
    }

    fn __contains__(&self, handle: i64) -> bool {
        Handle::try_from(handle).is_ok_and(|h| self.inner.contains(h))
    }

    fn push(&mut self, priority: f64) -> PyResult<Handle> {
        self.inner.push(priority).map_err(to_py_err)
    }

    /// Insert many priorities; returns their handles in input order.
    fn push_many(&mut self, priorities: Vec<f64>) -> PyResult<Vec<Handle>> {
        self.inner.push_many(&priorities).map_err(to_py_err)
    }

    /// ``(handle, priority)`` of the top entry, or ``None`` if empty.
    fn peek(&self) -> Option<(Handle, f64)> {
        self.inner.peek()
    }

    fn pop(&mut self) -> PyResult<(Handle, f64)> {
        self.inner
            .pop()
            .ok_or_else(|| PyIndexError::new_err("pop from an empty heap"))
    }

    /// Fused push-then-pop. Returns ``None`` if the new entry would be popped
    /// immediately (heap unchanged); otherwise pops the top entry, reassigns
    /// its handle to the new entry and returns ``(handle, popped_priority)``.
    fn push_pop(&mut self, priority: f64) -> PyResult<Option<(Handle, f64)>> {
        self.inner.push_pop(priority).map_err(to_py_err)
    }

    /// Pop up to ``n`` entries in priority order.
    fn pop_many(&mut self, n: usize) -> Vec<(Handle, f64)> {
        self.inner.pop_many(n)
    }

    fn priority(&self, handle: Handle) -> PyResult<f64> {
        self.inner
            .priority(handle)
            .ok_or_else(|| to_py_err(Error::InvalidHandle(handle)))
    }

    /// Set the priority of ``handle``; returns the previous priority.
    fn update(&mut self, handle: Handle, priority: f64) -> PyResult<f64> {
        self.inner.update(handle, priority).map_err(to_py_err)
    }

    /// Set many priorities at once (atomic; last write wins on duplicates).
    fn update_many(&mut self, handles: Vec<Handle>, priorities: Vec<f64>) -> PyResult<()> {
        self.inner
            .update_many(&handles, &priorities)
            .map_err(to_py_err)
    }

    /// Remove ``handle``; returns its priority.
    fn remove(&mut self, handle: Handle) -> PyResult<f64> {
        self.inner.remove(handle).map_err(to_py_err)
    }

    /// Remove every entry. Handle numbering restarts at 0.
    fn clear(&mut self) {
        self.inner.clear();
    }

    /// Live ``(handle, priority)`` pairs in internal heap order (not sorted).
    fn items(&self) -> Vec<(Handle, f64)> {
        self.inner.iter().collect()
    }

    fn copy(&self) -> Self {
        self.clone()
    }

    fn __copy__(&self) -> Self {
        self.clone()
    }

    fn __deepcopy__(&self, _memo: &Bound<'_, PyAny>) -> Self {
        self.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "IndexedHeap(len={}, reverse={})",
            self.inner.len(),
            if self.reverse() { "True" } else { "False" }
        )
    }
}

#[pymodule]
fn _iheap(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyIndexedHeap>()?;
    Ok(())
}
