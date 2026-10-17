"""Pure-Python implementation of :class:`IndexedHeap`.

Used where the native extension is unavailable: interpreters without a
prebuilt wheel (for example free-threaded CPython 3.13, PyPy), or on request
(``backend="python"``, ``IHEAP_BACKEND=python``). It has the same API, handle
semantics, argument rules and error behavior as the native core, at
pure-Python speed.

Layout: an indexed binary min-heap. ``_heap`` lists handles in heap order;
``_keys[h]`` is handle ``h``'s key (``None`` if vacant) and ``_pos[h]`` its
index in ``_heap``. A max-heap stores negated priorities. Each heap has a lock,
so concurrent calls serialize, as with the native core.

Exception safety: every sift is *planned* first (all comparisons, no writes)
and only then *applied* (writes, no comparisons). Float comparisons cannot
fail, but :class:`~iheap.ObjectHeap` reuses this class with user objects whose
comparisons may raise; planning first means such an error leaves the heap
untouched. Batch operations additionally snapshot the arrays when comparisons
may raise.
"""

from __future__ import annotations

import operator
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

MAX_LEN = 2**32 - 1
_NAN_MESSAGE = "priority must not be NaN"


def _handle_arg(handle: Any) -> int:
    """An int-like handle (``__index__``), as the native core accepts."""
    try:
        return operator.index(handle)
    except TypeError:
        raise TypeError(f"handle must be an integer, not {type(handle).__name__}") from None


class IndexedHeap:
    __slots__ = ("_heap", "_keys", "_pos", "_free", "_sign", "_lock")

    #: Whether key comparisons can raise (user objects); enables snapshots.
    _COMPARISONS_MAY_RAISE = False

    def __init__(self, *, reverse: Any = False, capacity: Any = 0) -> None:
        capacity = operator.index(capacity)
        if not 0 <= capacity <= MAX_LEN:
            raise ValueError(f"capacity must be in 0..={MAX_LEN}")
        self._sign = -1.0 if reverse else 1.0
        self._lock = self._new_lock()
        self._reset()

    @staticmethod
    def _new_lock() -> Any:
        return threading.Lock()

    # -- conversions (subclasses override) ---------------------------------

    def _key(self, priority: Any) -> Any:
        if isinstance(priority, (str, bytes, bytearray)):
            raise TypeError(f"must be real number, not {type(priority).__name__}")
        value = float(priority)
        if value != value:
            raise ValueError(_NAN_MESSAGE)
        return value * self._sign

    def _decode(self, key: Any) -> Any:
        return key * self._sign

    def _check(self, keys: list[Any]) -> None:
        """Called with the lock held, before mutating, with all new keys."""

    # -- internals (callers hold the lock) ----------------------------------

    def _reset(self) -> None:
        self._heap: list[int] = []
        self._keys: list[Any] = []
        self._pos: list[int] = []
        self._free: list[int] = []

    def _live(self, handle: Any) -> int:
        """The live handle that ``handle`` names; ``KeyError`` if none."""
        h = _handle_arg(handle)
        if 0 <= h < len(self._keys) and self._keys[h] is not None:
            return h
        raise KeyError(h)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Restore the arrays if a (user) comparison raises mid-operation."""
        if not self._COMPARISONS_MAY_RAISE:
            yield
            return
        saved = (self._heap.copy(), self._keys.copy(), self._pos.copy(), self._free.copy())
        try:
            yield
        except BaseException:
            self._heap, self._keys, self._pos, self._free = saved
            raise

    def _allocate(self, key: Any) -> int:
        """Append a new entry at the end of the heap array; returns its handle."""
        if self._free:
            handle = self._free.pop()
            self._keys[handle] = key
            self._pos[handle] = len(self._heap)
        else:
            if len(self._keys) >= MAX_LEN:
                raise OverflowError(f"heap cannot hold more than {MAX_LEN} entries")
            handle = len(self._keys)
            self._keys.append(key)
            self._pos.append(len(self._heap))
        self._heap.append(handle)
        return handle

    def _release(self, handle: int) -> None:
        self._keys[handle] = None
        self._pos[handle] = -1
        self._free.append(handle)

    def _plan_up(self, index: int, key: Any) -> int:
        """Index where ``key`` would settle moving up from ``index`` (reads only)."""
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
        """Child indices that ``key`` would descend through from ``index``,
        within the first ``n`` heap slots (reads only)."""
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
        """Give ``handle`` (at ``index``) the key ``key`` and settle it within
        the first ``n`` slots: plans in either direction, then writes."""
        if index > 0 and key < self._keys[self._heap[(index - 1) >> 1]]:
            target = self._plan_up(index, key)
            self._keys[handle] = key
            self._apply_up(index, target, handle)
        else:
            path = self._plan_down(index, key, n)
            self._keys[handle] = key
            self._apply_down(index, path, handle)

    def _heapify(self) -> None:
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

    def _items(self) -> list[tuple[int, Any]]:
        decode = self._decode
        return [(h, decode(k)) for h, k in enumerate(self._keys) if k is not None]

    @staticmethod
    def _prefer_rebuild(k: int, n: int, cost: int) -> bool:
        """As in the native core: rebuild when ``k`` updates cost more."""
        return n > 1 and k * (n.bit_length() - 1) >= cost

    # -- public API -----------------------------------------------------------

    @property
    def reverse(self) -> bool:
        return self._sign < 0

    @property
    def handle_bound(self) -> int:
        return len(self._keys)

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)

    def __contains__(self, handle: Any) -> bool:
        h = _handle_arg(handle)
        with self._lock:
            return 0 <= h < len(self._keys) and self._keys[h] is not None

    def push(self, priority: Any) -> int:
        key = self._key(priority)
        with self._lock:
            self._check([key])
            return self._push(key)

    def push_many(self, priorities: Iterable[Any]) -> list[int]:
        keys = [self._key(p) for p in priorities]  # validate all first: atomic
        with self._lock, self._transaction():
            self._check(keys)
            bound = len(self._keys) + max(0, len(keys) - len(self._free))
            if self._prefer_rebuild(len(keys), len(self._heap) + len(keys), bound):
                handles = [self._allocate(key) for key in keys]
                self._heapify()
                return handles
            return [self._push(key) for key in keys]

    def peek(self) -> tuple[int, Any] | None:
        with self._lock:
            if not self._heap:
                return None
            top = self._heap[0]
            return top, self._decode(self._keys[top])

    def pop(self) -> tuple[int, Any]:
        with self._lock:
            return self._pop()

    def push_pop(self, priority: Any) -> tuple[int, Any] | None:
        key = self._key(priority)
        with self._lock:
            heap, keys = self._heap, self._keys
            if not heap or keys[heap[0]] >= key:
                return None
            top = heap[0]
            old = keys[top]
            path = self._plan_down(0, key, len(heap))
            keys[top] = key
            self._apply_down(0, path, top)
            return top, self._decode(old)

    def pop_many(self, n: Any) -> list[tuple[int, Any]]:
        n = max(0, operator.index(n))
        with self._lock:
            return [self._pop() for _ in range(min(n, len(self._heap)))]

    def priority(self, handle: Any) -> Any:
        with self._lock:
            return self._decode(self._keys[self._live(handle)])

    def update(self, handle: Any, priority: Any) -> Any:
        key = self._key(priority)
        with self._lock:
            h = self._live(handle)
            self._check([key])
            old = self._keys[h]
            self._move(self._pos[h], h, key, len(self._heap))
            return self._decode(old)

    def update_many(self, handles: Iterable[Any], priorities: Iterable[Any]) -> None:
        handles, priorities = list(handles), list(priorities)
        if len(handles) != len(priorities):
            raise ValueError(f"got {len(handles)} handles but {len(priorities)} priorities")
        keys = [self._key(p) for p in priorities]
        with self._lock, self._transaction():
            hs = [self._live(h) for h in handles]  # validate all first: atomic
            self._check(keys)
            if self._prefer_rebuild(len(keys), len(self._heap), len(self._keys)):
                for h, key in zip(hs, keys):
                    self._keys[h] = key
                self._heapify()
            else:
                for h, key in zip(hs, keys):
                    self._move(self._pos[h], h, key, len(self._heap))

    def remove(self, handle: Any) -> Any:
        with self._lock:
            h = self._live(handle)
            heap, keys = self._heap, self._keys
            index, n = self._pos[h], len(heap)
            key = keys[h]
            last = heap[n - 1]
            if index == n - 1:
                heap.pop()
            else:
                # Plan where `last` settles at `index`, among the n - 1 slots
                # that remain, before changing anything.
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

    def clear(self) -> None:
        with self._lock:
            self._reset()

    def items(self) -> list[tuple[int, Any]]:
        with self._lock:
            return self._items()

    def copy(self) -> IndexedHeap:
        new = type(self)(reverse=self.reverse)
        with self._lock:
            new._heap = self._heap.copy()
            new._keys = self._keys.copy()
            new._pos = self._pos.copy()
            new._free = self._free.copy()
        return new

    __copy__ = copy

    def __deepcopy__(self, memo: Any) -> IndexedHeap:
        return self.copy()

    @classmethod
    def _from_state(
        cls, reverse: bool, bound: int, entries: list[tuple[int, Any]], free: list[int]
    ) -> IndexedHeap:
        """Rebuild from ``__reduce__`` state, reproducing handle reuse exactly."""
        if sorted([h for h, _ in entries] + list(free)) != list(range(bound)):
            raise ValueError("invalid heap state: handles must cover the bound exactly once")
        heap = cls(reverse=reverse)
        keys = [heap._key(p) for _, p in entries]
        heap._check(keys)
        heap._keys = [None] * bound
        heap._pos = [-1] * bound
        for (h, _), key in zip(entries, keys):
            heap._keys[h] = key
        heap._heap = [h for h, _ in entries]
        heap._free = list(free)
        heap._heapify()
        return heap

    def __reduce__(self) -> tuple[Any, ...]:
        with self._lock:
            state = (self.reverse, len(self._keys), self._items(), list(self._free))
        return (type(self)._from_state, state)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(len={len(self._heap)}, reverse={self.reverse})"
