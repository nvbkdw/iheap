"""Indexed priority queues with a Rust core.

* :class:`IndexedHeap` -- low-level native heap of float priorities addressed by
  integer handles. Holds no Python objects.
* :class:`PQDict` -- ``pqdict``-style mapping of keys to values ordered by
  priority. Keys and values live in Python; only priorities reach Rust.
"""

from importlib.metadata import version as _version

from ._iheap import IndexedHeap
from ._pqdict import PQDict, maxpq, minpq, nlargest, nsmallest

__all__ = ["IndexedHeap", "PQDict", "maxpq", "minpq", "nlargest", "nsmallest"]
__version__ = _version("iheap")
