//! Python bindings for [`iheap_core::IndexedHeap`].
//!
//! Only `f64` priorities and integer handles cross the FFI boundary; any data
//! associated with a handle is owned by the Python caller.
//!
//! The module supports free-threaded Python: each heap is guarded by a mutex,
//! so concurrent calls on one heap from several threads are serialized (like
//! operations on a built-in `list`) instead of failing. The lock is never held
//! while Python code runs (arguments are converted before it is taken), so it
//! cannot deadlock against the interpreter.

use iheap_core::{Error, Handle, IndexedHeap as CoreHeap, Order};
use pyo3::exceptions::{PyIndexError, PyKeyError, PyOverflowError, PyValueError};
use pyo3::prelude::*;
use std::sync::{Mutex, MutexGuard, PoisonError};

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
#[pyclass(module = "iheap._iheap", name = "IndexedHeap", frozen)]
struct PyIndexedHeap {
    inner: Mutex<CoreHeap>,
}

impl PyIndexedHeap {
    fn wrap(heap: CoreHeap) -> Self {
        Self {
            inner: Mutex::new(heap),
        }
    }

    /// Every method body is panic-free on valid state, so a poisoned lock
    /// still guards a consistent heap.
    fn heap(&self) -> MutexGuard<'_, CoreHeap> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

#[pymethods]
impl PyIndexedHeap {
    #[new]
    #[pyo3(signature = (*, reverse = false, capacity = 0))]
    fn new(reverse: bool, capacity: usize) -> Self {
        let order = if reverse { Order::Max } else { Order::Min };
        Self::wrap(CoreHeap::with_capacity(order, capacity))
    }

    /// True for a max-heap.
    #[getter]
    fn reverse(&self) -> bool {
        self.heap().order() == Order::Max
    }

    /// Every live handle is strictly below this value.
    #[getter]
    fn handle_bound(&self) -> usize {
        self.heap().handle_bound()
    }

    fn __len__(&self) -> usize {
        self.heap().len()
    }

    fn __bool__(&self) -> bool {
        !self.heap().is_empty()
    }

    fn __contains__(&self, handle: i64) -> bool {
        Handle::try_from(handle).is_ok_and(|h| self.heap().contains(h))
    }

    fn push(&self, priority: f64) -> PyResult<Handle> {
        self.heap().push(priority).map_err(to_py_err)
    }

    /// Insert many priorities; returns their handles in input order.
    fn push_many(&self, priorities: Vec<f64>) -> PyResult<Vec<Handle>> {
        self.heap().push_many(&priorities).map_err(to_py_err)
    }

    /// ``(handle, priority)`` of the top entry, or ``None`` if empty.
    fn peek(&self) -> Option<(Handle, f64)> {
        self.heap().peek()
    }

    fn pop(&self) -> PyResult<(Handle, f64)> {
        self.heap()
            .pop()
            .ok_or_else(|| PyIndexError::new_err("pop from an empty heap"))
    }

    /// Fused push-then-pop. Returns ``None`` if the new entry would be popped
    /// immediately (heap unchanged); otherwise pops the top entry, reassigns
    /// its handle to the new entry and returns ``(handle, popped_priority)``.
    fn push_pop(&self, priority: f64) -> PyResult<Option<(Handle, f64)>> {
        self.heap().push_pop(priority).map_err(to_py_err)
    }

    /// Pop up to ``n`` entries in priority order.
    fn pop_many(&self, n: usize) -> Vec<(Handle, f64)> {
        self.heap().pop_many(n)
    }

    fn priority(&self, handle: Handle) -> PyResult<f64> {
        self.heap()
            .priority(handle)
            .ok_or_else(|| to_py_err(Error::InvalidHandle(handle)))
    }

    /// Set the priority of ``handle``; returns the previous priority.
    fn update(&self, handle: Handle, priority: f64) -> PyResult<f64> {
        self.heap().update(handle, priority).map_err(to_py_err)
    }

    /// Set many priorities at once (atomic; last write wins on duplicates).
    fn update_many(&self, handles: Vec<Handle>, priorities: Vec<f64>) -> PyResult<()> {
        self.heap()
            .update_many(&handles, &priorities)
            .map_err(to_py_err)
    }

    /// Remove ``handle``; returns its priority.
    fn remove(&self, handle: Handle) -> PyResult<f64> {
        self.heap().remove(handle).map_err(to_py_err)
    }

    /// Remove every entry. Handle numbering restarts at 0.
    fn clear(&self) {
        self.heap().clear();
    }

    /// Live ``(handle, priority)`` pairs in handle order (not priority order).
    fn items(&self) -> Vec<(Handle, f64)> {
        self.heap().iter().collect()
    }

    fn copy(&self) -> Self {
        Self::wrap(self.heap().clone())
    }

    fn __copy__(&self) -> Self {
        self.copy()
    }

    fn __deepcopy__(&self, _memo: &Bound<'_, PyAny>) -> Self {
        self.copy()
    }

    fn __repr__(&self) -> String {
        let heap = self.heap();
        let reverse = if heap.order() == Order::Max {
            "True"
        } else {
            "False"
        };
        format!("IndexedHeap(len={}, reverse={reverse})", heap.len())
    }
}

#[pymodule(gil_used = false)]
fn _iheap(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyIndexedHeap>()?;
    Ok(())
}
