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

    # Exception-safe internals: user comparisons may raise, so every sift is
    # *planned* first (all comparisons, no writes) and only then *applied*
    # (writes, no comparisons). A raising comparison leaves the heap as it was.

    def _plan_up(self, index: int, key: Any) -> int:
        """Index where ``key`` would settle moving up from ``index``."""
        heap, keys = self._heap, self._keys
        while index > 0:
            parent = (index - 1) >> 1
            if key >= keys[heap[parent]]:
                break
            index = parent
        return index

    def _apply_up(self, index: int, target: int, handle: int) -> None:
        heap, pos = self._heap, self._pos
        while index > target:
            parent = (index - 1) >> 1
            ph = heap[parent]
            heap[index] = ph
            pos[ph] = index
            index = parent
        heap[index] = handle
        pos[handle] = index

    def _plan_down(self, index: int, key: Any, n: int) -> list[int]:
        """Child indices ``key`` would descend through from ``index``, within
        the first ``n`` slots."""
        heap, keys = self._heap, self._keys
        path = []
        while True:
            child = 2 * index + 1
            if child >= n:
                return path
            ck = keys[heap[child]]
            right = child + 1
            if right < n:
                rk = keys[heap[right]]
                if rk < ck:
                    child, ck = right, rk
            if ck >= key:
                return path
            path.append(child)
            index = child

    def _apply_down(self, index: int, path: list[int], handle: int) -> None:
        heap, pos = self._heap, self._pos
        for child in path:
            ch = heap[child]
            heap[index] = ch
            pos[ch] = index
            index = child
        heap[index] = handle
        pos[handle] = index

    def _move(self, index: int, handle: int, key: Any, n: int) -> None:
        if index > 0 and key < self._keys[self._heap[(index - 1) >> 1]]:
            target = self._plan_up(index, key)
            self._keys[handle] = key
            self._apply_up(index, target, handle)
        else:
            path = self._plan_down(index, key, n)
            self._keys[handle] = key
            self._apply_down(index, path, handle)

    def _heapify(self) -> None:
        # Only used inside a `_transaction`, which restores on failure.
        heap, keys = self._heap, self._keys
        for index, handle in enumerate(heap):
            self._pos[handle] = index
        n = len(heap)
        for index in reversed(range(n // 2)):
            handle = heap[index]
            self._apply_down(index, self._plan_down(index, keys[handle], n), handle)

    def _push(self, key: Any) -> int:
        target = self._plan_up(len(self._heap), key)  # as if already appended
        handle = self._allocate(key)
        self._apply_up(len(self._heap) - 1, target, handle)
        return handle

    def _pop(self) -> tuple[int, Any]:
        heap, keys = self._heap, self._keys
        if not heap:
            raise IndexError("pop from an empty heap")
        top = heap[0]
        n = len(heap)
        if n > 1:
            last = heap[n - 1]
            path = self._plan_down(0, keys[last], n - 1)  # `last` moves to the root
            heap.pop()
            self._apply_down(0, path, last)
        else:
            heap.pop()
        key = keys[top]
        self._release(top)
        return top, self._decode(key)

    def _push_pop(self, key: Any) -> tuple[int, Any]:
        top = self._heap[0]
        old = self._keys[top]
        path = self._plan_down(0, key, len(self._heap))
        self._keys[top] = key
        self._apply_down(0, path, top)
        return top, self._decode(old)

    def _remove(self, h: int) -> Any:
        heap, keys = self._heap, self._keys
        index, n = self._pos[h], len(heap)
        key = keys[h]
        last = heap[n - 1]
        if index == n - 1:
            heap.pop()
        else:
            # Plan where `last` settles at `index`, among the n - 1 slots that
            # remain, before changing anything.
            last_key = keys[last]
            if index > 0 and last_key < keys[heap[(index - 1) >> 1]]:
                target = self._plan_up(index, last_key)
                heap.pop()
                self._apply_up(index, target, last)
            else:
                path = self._plan_down(index, last_key, n - 1)
                heap.pop()
                self._apply_down(index, path, last)
        self._release(h)
        return self._decode(key)
