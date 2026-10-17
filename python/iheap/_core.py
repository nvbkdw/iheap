"""Selects the ``IndexedHeap`` implementation.

The native extension (Rust) is used whenever it can be imported. Otherwise,
or when the environment variable ``IHEAP_PURE_PYTHON`` is set to a non-empty
value, the pure-Python implementation with the same API is used.
"""

import os

from ._pyheap import IndexedHeap

NATIVE = False
if not os.environ.get("IHEAP_PURE_PYTHON"):
    try:
        from ._iheap import IndexedHeap  # type: ignore[assignment]  # noqa: F811
    except ImportError:  # no native wheel for this interpreter, e.g. CPython 3.13t
        pass
    else:
        NATIVE = True

__all__ = ["NATIVE", "IndexedHeap"]
