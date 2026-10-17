//! Interior mutability for a heap owned by a Python object.
//!
//! Python may call methods of one object from several threads, so the heap
//! needs a synchronization strategy. Which one is cheapest depends on the
//! interpreter, so it is chosen when the extension is compiled:
//!
//! * **GIL builds** (the `abi3` wheels): every call already runs while holding
//!   the GIL, and a heap operation never runs Python code, so no other thread
//!   can enter between start and end of an operation. The GIL *is* the lock:
//!   no mutex and no borrow flag.
//! * **Free-threaded builds** (`cp314t`): no GIL, so a `Mutex` serializes
//!   concurrent calls on one heap.
//!
//! Access goes through [`SharedHeap::with`], whose closure receives the heap
//! for the duration of one operation; borrows cannot escape or overlap.

use iheap_core::IndexedHeap;

#[cfg(not(Py_GIL_DISABLED))]
pub(crate) struct SharedHeap(std::cell::UnsafeCell<IndexedHeap>);

// SAFETY: on GIL builds, `with` is only called from `#[pymethods]`, which run
// while holding the GIL. Closures passed to `with` only manipulate the Rust
// heap: they neither run Python code nor release the GIL, so the GIL is held
// continuously for the whole access and no other thread can access the heap
// at the same time. PyO3 refuses to load the module in sub-interpreters with
// their own GIL; legacy sub-interpreters share the main GIL, so they are fine.
#[cfg(not(Py_GIL_DISABLED))]
unsafe impl Sync for SharedHeap {}

#[cfg(not(Py_GIL_DISABLED))]
impl SharedHeap {
    pub(crate) fn new(heap: IndexedHeap) -> Self {
        Self(std::cell::UnsafeCell::new(heap))
    }

    /// Runs `f` with exclusive access to the heap. `f` must not run Python
    /// code (see the `Sync` impl), and must not call `with` on the same heap.
    #[inline(always)]
    pub(crate) fn with<R>(&self, f: impl FnOnce(&mut IndexedHeap) -> R) -> R {
        // SAFETY: exclusive for the duration of `f`, per the `Sync` impl;
        // `with` is never nested on one heap.
        f(unsafe { &mut *self.0.get() })
    }
}

#[cfg(Py_GIL_DISABLED)]
pub(crate) struct SharedHeap(std::sync::Mutex<IndexedHeap>);

#[cfg(Py_GIL_DISABLED)]
impl SharedHeap {
    pub(crate) fn new(heap: IndexedHeap) -> Self {
        Self(std::sync::Mutex::new(heap))
    }

    /// Runs `f` with exclusive access to the heap. The lock is never held
    /// while Python code runs (arguments are converted before `with`), so it
    /// cannot deadlock against the interpreter.
    #[inline(always)]
    pub(crate) fn with<R>(&self, f: impl FnOnce(&mut IndexedHeap) -> R) -> R {
        // Heap operations do not panic on valid state, so even a poisoned lock
        // (a panic surfaced as a Python exception) guards a consistent heap.
        let mut heap = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        f(&mut heap)
    }
}
