"""Indexed priority queues with a Rust core.

* :class:`IndexedHeap` -- the native heap of float priorities addressed by
  integer handles. Holds no Python objects.
* :class:`PQDict` -- ``pqdict``-style mapping of keys to values ordered by
  priority. Keys and values live in Python; only priorities reach Rust.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from ._iheap import MAX_LEN, IndexedHeap
from ._pqdict import PQDict, maxpq, minpq, nlargest, nsmallest

__all__ = ["MAX_LEN", "IndexedHeap", "PQDict", "maxpq", "minpq", "nlargest", "nsmallest"]

try:
    __version__ = _version("iheap")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "0+unknown"
