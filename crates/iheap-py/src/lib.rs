//! Python bindings for [`iheap_core::IndexedHeap`].
//!
//! Only `f64` priorities and integer handles cross the FFI boundary; any data
//! associated with a handle is owned by the Python caller. Arguments are
//! converted before the heap is touched, and conversion rules match the
//! pure-Python core: handles are any int-like (`__index__`), batches any
//! iterable, flags truthy.
//!
//! Concurrent calls on one heap from several threads are serialized (like
//! operations on a built-in `list`); how depends on the interpreter the
//! extension is built for, see [`shared`].

mod shared;

use iheap_core::{Error, Handle, IndexedHeap as CoreHeap, Order, MAX_LEN};
use pyo3::exceptions::{
    PyIndexError, PyKeyError, PyMemoryError, PyOverflowError, PyTypeError, PyValueError,
};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};
use shared::SharedHeap;

fn to_py_err(err: Error) -> PyErr {
    match err {
        Error::InvalidHandle(h) => PyKeyError::new_err(h),
        Error::NanPriority | Error::LengthMismatch { .. } | Error::InvalidParts(_) => {
            PyValueError::new_err(err.to_string())
        }
        Error::CapacityOverflow => PyOverflowError::new_err(err.to_string()),
        Error::OutOfMemory => PyMemoryError::new_err(err.to_string()),
    }
}

/// A handle argument: `Ok(None)` for an int-like outside the handle range
/// (it cannot name a live entry), `TypeError` for a non-integer.
#[inline]
fn handle_arg(obj: &Bound<'_, PyAny>) -> PyResult<Option<Handle>> {
    // Fast path: an exact `int` in range (the overwhelmingly common case).
    if let Ok(int) = obj.cast_exact::<pyo3::types::PyInt>() {
        if let Ok(h) = int.extract::<Handle>() {
            return Ok(Some(h));
        }
    }
    match obj.extract::<Handle>() {
        Ok(h) => Ok(Some(h)),
        Err(e) if e.is_instance_of::<PyOverflowError>(obj.py()) => Ok(None),
        Err(e) if e.is_instance_of::<PyTypeError>(obj.py()) => Err(PyTypeError::new_err(format!(
            "handle must be an integer, not {}",
            obj.get_type().name()?
        ))),
        Err(e) => Err(e),
    }
}

/// A handle that must name a live entry; `KeyError` if it cannot.
fn live_handle(obj: &Bound<'_, PyAny>) -> PyResult<Handle> {
    handle_arg(obj)?.ok_or_else(|| PyKeyError::new_err(obj.clone().unbind()))
}

/// Floats from any iterable; sequences take PyO3's fast path.
fn floats(obj: &Bound<'_, PyAny>) -> PyResult<Vec<f64>> {
    if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
        return obj.extract();
    }
    obj.try_iter()?.map(|item| item?.extract()).collect()
}

/// Handles from any iterable, for batch updates.
fn handles(obj: &Bound<'_, PyAny>) -> PyResult<Vec<Handle>> {
    obj.try_iter()?.map(|item| live_handle(&item?)).collect()
}

/// A non-negative count; `None` for negative input.
fn count(obj: &Bound<'_, PyAny>) -> PyResult<Option<usize>> {
    let n: i128 = obj.extract()?;
    Ok(usize::try_from(n).ok())
}

/// Indexed priority queue of float priorities addressed by integer handles.
///
/// ``push`` returns a handle that stays valid until the entry is popped or
/// removed. Handles are small non-negative integers below ``handle_bound``
/// and are recycled, so callers can keep per-entry data in a list indexed by
/// handle.
#[pyclass(module = "iheap._iheap", name = "IndexedHeap", frozen)]
struct PyIndexedHeap(SharedHeap);

// The struct lives inside a Python object, which CPython only guarantees to
// be 8-byte aligned on 32-bit platforms; over-aligned contents would be
// misaligned there (and crash on aligned SIMD moves).
const _: () = assert!(std::mem::align_of::<PyIndexedHeap>() <= 8);

impl PyIndexedHeap {
    fn wrap(heap: CoreHeap) -> Self {
        Self(SharedHeap::new(heap))
    }
}

fn order(reverse: bool) -> Order {
    if reverse {
        Order::Max
    } else {
        Order::Min
    }
}

#[pymethods]
impl PyIndexedHeap {
    #[new]
    #[pyo3(signature = (*, reverse = None, capacity = None))]
    fn new(
        reverse: Option<&Bound<'_, PyAny>>,
        capacity: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let reverse = match reverse {
            Some(flag) => flag.is_truthy()?,
            None => false,
        };
        let capacity = match capacity {
            Some(c) => count(c)?.filter(|&c| c <= MAX_LEN).ok_or_else(|| {
                PyValueError::new_err(format!("capacity must be in 0..={MAX_LEN}"))
            })?,
            None => 0,
        };
        let heap = CoreHeap::try_with_capacity(order(reverse), capacity).map_err(to_py_err)?;
        Ok(Self::wrap(heap))
    }

    /// Rebuild from ``__reduce__`` state (used by pickle and copy).
    #[staticmethod]
    fn _from_state(
        reverse: bool,
        bound: usize,
        entries: Vec<(Handle, f64)>,
        free: Vec<Handle>,
    ) -> PyResult<Self> {
        CoreHeap::from_parts(order(reverse), bound, &entries, &free)
            .map(Self::wrap)
            .map_err(to_py_err)
    }

    fn __reduce__<'py>(
        slf: &Bound<'py, Self>,
    ) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyTuple>)> {
        let (reverse, bound, entries, free) = slf.get().0.with(|h| {
            let entries: Vec<(Handle, f64)> = h.iter().collect();
            (
                h.order() == Order::Max,
                h.handle_bound(),
                entries,
                h.free_handles().to_vec(),
            )
        });
        let rebuild = slf.get_type().getattr("_from_state")?;
        let args = (reverse, bound, entries, free).into_pyobject(slf.py())?;
        Ok((rebuild, args))
    }

    /// True for a max-heap.
    #[getter]
    fn reverse(&self) -> bool {
        self.0.with(|h| h.order() == Order::Max)
    }

    /// Every live handle is strictly below this value.
    #[getter]
    fn handle_bound(&self) -> usize {
        self.0.with(|h| h.handle_bound())
    }

    fn __len__(&self) -> usize {
        self.0.with(|h| h.len())
    }

    fn __bool__(&self) -> bool {
        !self.0.with(|h| h.is_empty())
    }

    fn __contains__(&self, handle: &Bound<'_, PyAny>) -> PyResult<bool> {
        Ok(handle_arg(handle)?.is_some_and(|handle| self.0.with(|h| h.contains(handle))))
    }

    fn push(&self, priority: f64) -> PyResult<Handle> {
        self.0.with(|h| h.push(priority)).map_err(to_py_err)
    }

    /// Insert many priorities (any iterable); returns their handles in order.
    fn push_many(&self, priorities: &Bound<'_, PyAny>) -> PyResult<Vec<Handle>> {
        let priorities = floats(priorities)?;
        self.0.with(|h| h.push_many(&priorities)).map_err(to_py_err)
    }

    /// ``(handle, priority)`` of the top entry, or ``None`` if empty.
    fn peek(&self) -> Option<(Handle, f64)> {
        self.0.with(|h| h.peek())
    }

    fn pop(&self) -> PyResult<(Handle, f64)> {
        self.0
            .with(|h| h.pop())
            .ok_or_else(|| PyIndexError::new_err("pop from an empty heap"))
    }

    /// Fused push-then-pop. Returns ``None`` if the new entry would be popped
    /// immediately (heap unchanged); otherwise pops the top entry, reassigns
    /// its handle to the new entry and returns ``(handle, popped_priority)``.
    fn push_pop(&self, priority: f64) -> PyResult<Option<(Handle, f64)>> {
        self.0.with(|h| h.push_pop(priority)).map_err(to_py_err)
    }

    /// Pop up to ``n`` entries in priority order (none if ``n <= 0``).
    fn pop_many(&self, n: &Bound<'_, PyAny>) -> PyResult<Vec<(Handle, f64)>> {
        let n = count(n)?.unwrap_or(0);
        Ok(self.0.with(|h| h.pop_many(n)))
    }

    fn priority(&self, handle: &Bound<'_, PyAny>) -> PyResult<f64> {
        let h = live_handle(handle)?;
        self.0
            .with(|heap| heap.priority(h))
            .ok_or_else(|| to_py_err(Error::InvalidHandle(h)))
    }

    /// Set the priority of ``handle``; returns the previous priority.
    fn update(&self, handle: &Bound<'_, PyAny>, priority: f64) -> PyResult<f64> {
        if priority.is_nan() {
            // Priority errors take precedence, even over an unusable handle.
            return Err(to_py_err(Error::NanPriority));
        }
        let h = live_handle(handle)?;
        self.0
            .with(|heap| heap.update(h, priority))
            .map_err(to_py_err)
    }

    /// Set many priorities at once (atomic; last write wins on duplicates).
    fn update_many(
        &self,
        handles_: &Bound<'_, PyAny>,
        priorities: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let priorities = floats(priorities)?;
        if priorities.iter().any(|p| p.is_nan()) {
            return Err(to_py_err(Error::NanPriority)); // priority errors first
        }
        let handles = handles(handles_)?;
        self.0
            .with(|h| h.update_many(&handles, &priorities))
            .map_err(to_py_err)
    }

    /// Remove ``handle``; returns its priority.
    fn remove(&self, handle: &Bound<'_, PyAny>) -> PyResult<f64> {
        let h = live_handle(handle)?;
        self.0.with(|heap| heap.remove(h)).map_err(to_py_err)
    }

    /// Remove every entry. Handle numbering restarts at 0.
    fn clear(&self) {
        self.0.with(|h| h.clear());
    }

    /// Live ``(handle, priority)`` pairs in handle order (not priority order).
    fn items(&self) -> Vec<(Handle, f64)> {
        self.0.with(|h| h.iter().collect())
    }

    fn copy(&self) -> Self {
        Self::wrap(self.0.with(|h| h.clone()))
    }

    fn __copy__(&self) -> Self {
        self.copy()
    }

    fn __deepcopy__(&self, _memo: &Bound<'_, PyAny>) -> Self {
        self.copy()
    }

    fn __repr__(&self) -> String {
        let (len, order) = self.0.with(|h| (h.len(), h.order()));
        let reverse = if order == Order::Max { "True" } else { "False" };
        format!("IndexedHeap(len={len}, reverse={reverse})")
    }
}

#[pymodule(gil_used = false)]
fn _iheap(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyIndexedHeap>()?;
    m.add("MAX_LEN", MAX_LEN)?;
    Ok(())
}
