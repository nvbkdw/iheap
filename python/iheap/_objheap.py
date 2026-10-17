"""``ObjectHeap``: an indexed heap over arbitrary comparable priorities.

The native and float cores only order numbers. ``ObjectHeap`` orders any
priorities that support ``<`` among themselves: strings, tuples (e.g.
``(priority, sequence)`` for FIFO ties), dates, ``Decimal`` exactly, and so
on. It reuses the pure-Python heap, which only ever compares keys with ``<``
and ``>=`` and plans every sift before writing; a max-heap wraps keys in
:class:`_Reversed`, which inverts both. It runs at pure-Python speed.
"""

from __future__ import annotations

import threading
from typing import Any

from ._pyheap import IndexedHeap as _PyHeap


class _Reversed:
    """Key wrapper that inverts the order of the wrapped priority."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __lt__(self, other: _Reversed) -> bool:
        return other.value < self.value

    def __ge__(self, other: _Reversed) -> bool:
        return not other.value < self.value


class _ReentrancyGuard:
    """A lock that raises instead of deadlocking when the thread holding it
    re-enters: user ``__lt__`` methods run while the heap is locked, and one
    that touches the same heap would otherwise hang (or corrupt it)."""

    __slots__ = ("_lock", "_owner")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: int | None = None

    def __enter__(self) -> None:
        me = threading.get_ident()
        if self._owner == me:
            raise RuntimeError("ObjectHeap used from within one of its own priority comparisons")
        self._lock.acquire()
        self._owner = me

    def __exit__(self, *exc: object) -> None:
        self._owner = None
        self._lock.release()


class ObjectHeap(_PyHeap):
    """Indexed heap whose priorities are any mutually comparable objects.

    Same API, handle semantics and thread safety as
    [`IndexedHeap`][iheap.IndexedHeap], but priorities are stored as given
    (not converted to ``float``) and ordered with ``<``. They must be totally
    ordered and comparable with each other.

    Every operation is atomic: if comparing priorities raises (for example
    ``(1, "a")`` against ``(1, 2)``), the exception propagates and the heap is
    unchanged.

    Raises:
        TypeError: Priorities cannot be compared with each other.
        ValueError: A priority is NaN-like (``p != p``).
        RuntimeError: A priority comparison used the same heap.

    Examples:
        >>> heap = ObjectHeap()
        >>> h = heap.push("banana")
        >>> _ = heap.push_many(["cherry", "apple"])
        >>> heap.pop()[1]
        'apple'
        >>> heap.update(h, "zucchini")
        'banana'
        >>> heap.peek()[1]
        'cherry'
    """

    __slots__ = ()

    _COMPARISONS_MAY_RAISE = True

    @staticmethod
    def _new_lock() -> Any:
        return _ReentrancyGuard()

    def _key(self, priority: Any) -> Any:
        if priority != priority:
            raise ValueError("priority must not be NaN")
        return _Reversed(priority) if self._sign < 0 else priority

    def _decode(self, key: Any) -> Any:
        return key.value if self._sign < 0 else key
