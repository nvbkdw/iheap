"""Pure-Python implementation of :class:`IndexedHeap`.

Used where the native extension is unavailable: interpreters without a
prebuilt wheel, notably free-threaded CPython 3.13 (PyO3 supports
free-threading from 3.14 on), PyPy, or ``IHEAP_PURE_PYTHON=1``. It has the
same API, handle semantics and error behavior as the native core, at
pure-Python speed.

Layout: an indexed binary min-heap. ``_heap`` lists handles in heap order;
``_keys[h]`` is handle ``h``'s key (``None`` if vacant) and ``_pos[h]`` its
index in ``_heap``. A max-heap stores negated priorities. Each heap has a lock,
so concurrent calls serialize, as with the native core.
"""

from __future__ import annotations

import math
import threading
from typing import Any

_NAN_MESSAGE = "priority must not be NaN"


class IndexedHeap:
    __slots__ = ("_heap", "_keys", "_pos", "_free", "_sign", "_lock")

    def __init__(self, *, reverse: bool = False, capacity: int = 0) -> None:
        self._sign = -1.0 if reverse else 1.0
        self._lock = threading.Lock()
        self._reset()

    # -- internals (callers hold the lock) ----------------------------------

    def _reset(self) -> None:
        self._heap: list[int] = []
        self._keys: list[float | None] = []
        self._pos: list[int] = []
        self._free: list[int] = []

    def _key(self, priority: Any) -> float:
        if isinstance(priority, (str, bytes, bytearray)):
            raise TypeError(f"must be real number, not {type(priority).__name__}")
        value = float(priority)
        if value != value:
            raise ValueError(_NAN_MESSAGE)
        return value * self._sign

    def _live(self, handle: int) -> float:
        keys = self._keys
        if not isinstance(handle, int):
            raise TypeError(f"handle must be int, not {type(handle).__name__}")
        if 0 <= handle < len(keys):
            key = keys[handle]
            if key is not None:
                return key
        raise KeyError(handle)

    def _allocate(self, key: float) -> int:
        if self._free:
            handle = self._free.pop()
            self._keys[handle] = key
            self._pos[handle] = len(self._heap)
        else:
            handle = len(self._keys)
            self._keys.append(key)
            self._pos.append(len(self._heap))
        self._heap.append(handle)
        return handle

    def _release(self, handle: int) -> None:
        self._keys[handle] = None
        self._pos[handle] = -1
        self._free.append(handle)

    def _sift_up(self, index: int) -> None:
        heap, keys, pos = self._heap, self._keys, self._pos
        handle = heap[index]
        key = keys[handle]
        while index > 0:
            parent = (index - 1) >> 1
            ph = heap[parent]
            if key >= keys[ph]:
                break
            heap[index] = ph
            pos[ph] = index
            index = parent
        heap[index] = handle
        pos[handle] = index

    def _sift_down(self, index: int) -> None:
        heap, keys, pos = self._heap, self._keys, self._pos
        n = len(heap)
        handle = heap[index]
        key = keys[handle]
        while True:
            child = 2 * index + 1
            if child >= n:
                break
            ck = keys[heap[child]]
            right = child + 1
            if right < n:
                rk = keys[heap[right]]
                if rk < ck:
                    child, ck = right, rk
            if ck >= key:
                break
            ch = heap[child]
            heap[index] = ch
            pos[ch] = index
            index = child
        heap[index] = handle
        pos[handle] = index

    def _fix(self, index: int) -> None:
        """Restore heap order around a node whose key changed arbitrarily."""
        if index > 0:
            keys, heap = self._keys, self._heap
            if keys[heap[index]] < keys[heap[(index - 1) >> 1]]:
                self._sift_up(index)
                return
        self._sift_down(index)

    def _heapify(self) -> None:
        for index, handle in enumerate(self._heap):
            self._pos[handle] = index
        for index in reversed(range(len(self._heap) // 2)):
            self._sift_down(index)

    def _pop(self) -> tuple[int, float]:
        heap = self._heap
        if not heap:
            raise IndexError("pop from an empty heap")
        top = heap[0]
        key = self._keys[top]
        last = heap.pop()
        if heap:
            heap[0] = last
            self._sift_down(0)
        self._release(top)
        return top, key * self._sign  # type: ignore[operator]

    @staticmethod
    def _prefer_rebuild(k: int, n: int) -> bool:
        return n > 1 and k * int(math.log2(n)) >= n

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

    def __contains__(self, handle: int) -> bool:
        if not isinstance(handle, int):
            raise TypeError(f"handle must be int, not {type(handle).__name__}")
        with self._lock:
            return 0 <= handle < len(self._keys) and self._keys[handle] is not None

    def push(self, priority: float) -> int:
        key = self._key(priority)
        with self._lock:
            handle = self._allocate(key)
            self._sift_up(len(self._heap) - 1)
            return handle

    def push_many(self, priorities: list[float]) -> list[int]:
        keys = [self._key(p) for p in priorities]  # validate all first: atomic
        with self._lock:
            rebuild = self._prefer_rebuild(len(keys), len(self._heap) + len(keys))
            handles = []
            for key in keys:
                handles.append(self._allocate(key))
                if not rebuild:
                    self._sift_up(len(self._heap) - 1)
            if rebuild:
                self._heapify()
            return handles

    def peek(self) -> tuple[int, float] | None:
        with self._lock:
            if not self._heap:
                return None
            top = self._heap[0]
            return top, self._keys[top] * self._sign  # type: ignore[operator]

    def pop(self) -> tuple[int, float]:
        with self._lock:
            return self._pop()

    def push_pop(self, priority: float) -> tuple[int, float] | None:
        key = self._key(priority)
        with self._lock:
            heap, keys = self._heap, self._keys
            if not heap or keys[heap[0]] >= key:  # type: ignore[operator]
                return None
            top = heap[0]
            old = keys[top]
            keys[top] = key
            self._sift_down(0)
            return top, old * self._sign  # type: ignore[operator]

    def pop_many(self, n: int) -> list[tuple[int, float]]:
        with self._lock:
            return [self._pop() for _ in range(min(n, len(self._heap)))]

    def priority(self, handle: int) -> float:
        with self._lock:
            return self._live(handle) * self._sign

    def update(self, handle: int, priority: float) -> float:
        key = self._key(priority)
        with self._lock:
            old = self._live(handle)
            self._keys[handle] = key
            self._fix(self._pos[handle])
            return old * self._sign

    def update_many(self, handles: list[int], priorities: list[float]) -> None:
        handles, priorities = list(handles), list(priorities)
        if len(handles) != len(priorities):
            raise ValueError(f"got {len(handles)} handles but {len(priorities)} priorities")
        keys = [self._key(p) for p in priorities]
        with self._lock:
            for handle in handles:  # validate all first: atomic
                self._live(handle)
            if self._prefer_rebuild(len(keys), len(self._heap)):
                for handle, key in zip(handles, keys):
                    self._keys[handle] = key
                self._heapify()
            else:
                for handle, key in zip(handles, keys):
                    self._keys[handle] = key
                    self._fix(self._pos[handle])

    def remove(self, handle: int) -> float:
        with self._lock:
            key = self._live(handle)
            heap = self._heap
            index = self._pos[handle]
            last = heap.pop()
            if index < len(heap):
                heap[index] = last
                self._pos[last] = index
                self._fix(index)
            self._release(handle)
            return key * self._sign

    def clear(self) -> None:
        with self._lock:
            self._reset()

    def items(self) -> list[tuple[int, float]]:
        with self._lock:
            sign = self._sign
            return [(h, k * sign) for h, k in enumerate(self._keys) if k is not None]

    def copy(self) -> IndexedHeap:
        new = IndexedHeap(reverse=self.reverse)
        with self._lock:
            new._heap = self._heap.copy()
            new._keys = self._keys.copy()
            new._pos = self._pos.copy()
            new._free = self._free.copy()
        return new

    __copy__ = copy

    def __deepcopy__(self, memo: Any) -> IndexedHeap:
        return self.copy()

    def __repr__(self) -> str:
        return f"IndexedHeap(len={len(self._heap)}, reverse={self.reverse})"
