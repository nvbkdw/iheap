"""Backends: which implementation of ``IndexedHeap`` serves numeric priorities.

* ``"native"`` -- the Rust core (lock-free on GIL builds, a mutex per heap on
  free-threaded builds), if a native wheel is installed for this interpreter;
* ``"python"`` -- the pure-Python core with the same API;
* ``"auto"`` -- native if available, else Python.

The process-wide default is ``"auto"``, overridable with the environment
variable ``IHEAP_BACKEND`` (``auto``/``native``/``python``; the older
``IHEAP_PURE_PYTHON=1`` means ``python``). Queues can override it with
``PQDict(..., backend=...)``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal

from ._pyheap import IndexedHeap as PythonIndexedHeap

try:
    from ._iheap import IndexedHeap as NativeIndexedHeap
except ImportError:  # no native wheel for this interpreter, e.g. CPython 3.13t
    NativeIndexedHeap = None  # type: ignore[assignment,misc]

Backend = Literal["auto", "native", "python"]
BACKENDS: tuple[Backend, ...] = ("auto", "native", "python")


def _default_backend() -> Backend:
    value = os.environ.get("IHEAP_BACKEND", "").strip().lower()
    if not value:
        return "python" if os.environ.get("IHEAP_PURE_PYTHON") else "auto"
    if value not in BACKENDS:
        raise ValueError(f"IHEAP_BACKEND must be one of {', '.join(BACKENDS)}, not {value!r}")
    return value  # type: ignore[return-value]


DEFAULT_BACKEND: Backend = _default_backend()


def heap_class(backend: Backend | None = None) -> type:
    """The ``IndexedHeap`` class for ``backend`` (``None``: the default)."""
    backend = DEFAULT_BACKEND if backend is None else backend
    if backend == "auto":
        return NativeIndexedHeap or PythonIndexedHeap
    if backend == "python":
        return PythonIndexedHeap
    if backend == "native":
        if NativeIndexedHeap is None:
            raise ImportError(
                "the native iheap core is not available for this interpreter; "
                "install a platform wheel or use backend='python'"
            )
        return NativeIndexedHeap
    raise ValueError(f"backend must be one of {', '.join(BACKENDS)}, not {backend!r}")


if TYPE_CHECKING:  # type checkers and the API docs see the native stub
    from ._iheap import IndexedHeap
else:
    IndexedHeap = heap_class()

#: Whether ``iheap.IndexedHeap`` (the default backend) is the native core.
NATIVE: bool = NativeIndexedHeap is not None and IndexedHeap is NativeIndexedHeap

__all__ = ["BACKENDS", "DEFAULT_BACKEND", "NATIVE", "Backend", "IndexedHeap", "heap_class"]
