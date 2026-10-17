"""``ObjectHeap``: an indexed heap over arbitrary comparable priorities.

The native and float cores only order numbers. ``ObjectHeap`` orders any
priorities that support ``<`` among themselves: strings, tuples (e.g.
``(priority, sequence)`` for FIFO ties), dates, ``Decimal`` exactly, and so
on. It reuses the pure-Python heap, which only ever compares keys with ``<``
and ``>=``; a max-heap wraps keys in :class:`_Reversed`, which inverts both.
It runs at pure-Python speed.
"""

from __future__ import annotations

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


class ObjectHeap(_PyHeap):
    """Indexed heap whose priorities are any mutually comparable objects.

    Same API, handle semantics and thread safety as
    [`IndexedHeap`][iheap.IndexedHeap], but priorities are stored as given
    (not converted to ``float``) and ordered with ``<``. They must be totally
    ordered and comparable with each other.

    Raises:
        TypeError: A new priority cannot be compared with those in the heap
            (checked against the current top before anything changes).
        ValueError: A priority is NaN-like (``p != p``).

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

    def _key(self, priority: Any) -> Any:
        if priority != priority:
            raise ValueError("priority must not be NaN")
        return _Reversed(priority) if self._sign < 0 else priority

    def _decode(self, key: Any) -> Any:
        return key.value if self._sign < 0 else key

    def _check(self, keys: list[Any]) -> None:
        # Mixing incomparable types (say `str` and `int`) raises inside a
        # sift, half-way through moving entries. Comparing every new key with
        # a reference first (the current top, else the first new key) makes
        # such operations fail with no effect.
        if self._heap:
            reference = self._keys[self._heap[0]]
        elif keys:
            reference = keys[0]
        else:
            return
        for key in keys:
            bool(key < reference)
