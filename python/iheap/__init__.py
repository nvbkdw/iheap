"""Indexed priority queues with a Rust core.

* :class:`IndexedHeap` -- low-level heap of float priorities addressed by
  integer handles. Holds no Python objects.
* :class:`ObjectHeap` -- the same, for any comparable priorities (strings,
  tuples, ...), in pure Python.
* :class:`PQDict` -- ``pqdict``-style mapping of keys to values ordered by
  priority. Keys and values live in Python; only priorities reach the core.

:data:`NATIVE` tells whether the Rust core is the default. Where no native
wheel exists for the interpreter (for example free-threaded CPython 3.13), a
pure-Python core with the same API is used instead. ``IHEAP_BACKEND``
(``auto``/``native``/``python``) and ``backend=`` arguments override the choice.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from ._core import BACKENDS, DEFAULT_BACKEND, NATIVE, IndexedHeap, heap_class
from ._objheap import ObjectHeap
from ._pqdict import PRIORITY_TYPES, PQDict, maxpq, minpq, nlargest, nsmallest

__all__ = [
    "BACKENDS",
    "DEFAULT_BACKEND",
    "NATIVE",
    "PRIORITY_TYPES",
    "heap_class",
    "IndexedHeap",
    "ObjectHeap",
    "PQDict",
    "maxpq",
    "minpq",
    "nlargest",
    "nsmallest",
]

try:
    __version__ = _version("iheap")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "0+unknown"
