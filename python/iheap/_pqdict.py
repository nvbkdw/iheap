"""``pqdict``-compatible priority queue dictionary on top of :class:`IndexedHeap`.

Storage layout: the native heap owns ``(priority, handle)`` pairs only. This
module owns everything else:

* ``_handles`` maps each key to its heap handle;
* ``_keys[h]`` / ``_values[h]`` hold the key and value of handle ``h``.

Handles are dense and recycled by the heap, so the two lists never grow beyond
the peak queue size.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, MutableMapping
from typing import Any, Generic, TypeVar

from ._iheap import IndexedHeap

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING: Any = object()


class PQDict(MutableMapping[K, V], Generic[K, V]):
    """A mapping of keys to values that also behaves as a priority queue.

    The priority of an item is ``key(value)`` if a ``key`` function is given,
    otherwise the value itself; it must be convertible to ``float`` and not NaN.
    By default the item with the smallest priority is on top; pass
    ``reverse=True`` for a max-queue. Ties are popped in unspecified order.

    Iteration (``for k in pq``, ``keys()``, ``items()``) is in arbitrary order;
    use :meth:`popkeys`, :meth:`popitems` etc. for priority order.
    """

    __slots__ = ("_heap", "_handles", "_keys", "_values", "_keyfn")

    def __init__(
        self,
        data: Mapping[K, V] | Iterable[tuple[K, V]] | None = None,
        *,
        key: Callable[[V], float] | None = None,
        reverse: bool = False,
    ) -> None:
        self._heap = IndexedHeap(reverse=reverse)
        self._handles: dict[K, int] = {}
        self._keys: list[Any] = []
        self._values: list[Any] = []
        self._keyfn = key
        if data is not None:
            self.update(data)

    @classmethod
    def fromkeys(
        cls,
        iterable: Iterable[K],
        value: V,
        *,
        key: Callable[[V], float] | None = None,
        reverse: bool = False,
    ) -> PQDict[K, V]:
        return cls(((k, value) for k in iterable), key=key, reverse=reverse)

    @property
    def keyfn(self) -> Callable[[V], float] | None:
        return self._keyfn

    @property
    def reverse(self) -> bool:
        return self._heap.reverse

    # -- internal helpers --------------------------------------------------

    def _priority(self, value: V) -> Any:
        return value if self._keyfn is None else self._keyfn(value)

    def _bind(self, handle: int, key: K, value: V) -> None:
        if handle == len(self._keys):
            self._keys.append(key)
            self._values.append(value)
        else:
            self._keys[handle] = key
            self._values[handle] = value
        self._handles[key] = handle

    def _unbind(self, handle: int) -> tuple[K, V]:
        """Forget a handle the heap already released; drops payload references."""
        key, value = self._keys[handle], self._values[handle]
        self._keys[handle] = self._values[handle] = None
        del self._handles[key]
        return key, value

    def _top_handle(self) -> int | None:
        top = self._heap.peek()
        return None if top is None else top[0]

    # -- mapping protocol --------------------------------------------------

    def __len__(self) -> int:
        return len(self._handles)

    def __iter__(self) -> Iterator[K]:
        return iter(self._handles)

    def __contains__(self, key: object) -> bool:
        return key in self._handles

    def __getitem__(self, key: K) -> V:
        return self._values[self._handles[key]]

    # The hot paths below inline ``_priority``/``_bind``/``_unbind``: a Python
    # call frame costs more than the native heap operation itself.

    def __setitem__(self, key: K, value: V) -> None:
        keyfn = self._keyfn
        priority = value if keyfn is None else keyfn(value)
        handle = self._handles.get(key)
        if handle is None:
            handle = self._heap.push(priority)
            keys = self._keys
            if handle == len(keys):
                keys.append(key)
                self._values.append(value)
            else:
                keys[handle] = key
                self._values[handle] = value
            self._handles[key] = handle
        else:
            self._heap.update(handle, priority)
            self._values[handle] = value

    def __delitem__(self, key: K) -> None:
        handle = self._handles[key]
        self._heap.remove(handle)
        del self._handles[key]
        self._keys[handle] = self._values[handle] = None

    def update(self, other: Any = (), /, **kwargs: V) -> None:  # type: ignore[override]
        """Insert or update many items with one native call per kind of change.

        New keys are bulk-inserted (``O(n)`` heapify when large); existing keys
        are bulk-updated. Either everything is applied or nothing is.
        """
        items: dict[K, V] = dict(other, **kwargs)  # last write wins, like dict
        if not self._handles:
            self._load(items)
            return
        new_items: list[tuple[K, V]] = []
        new_priorities: list[Any] = []
        old_handles: list[int] = []
        old_values: list[V] = []
        old_priorities: list[Any] = []
        for k, v in items.items():
            priority = self._priority(v)
            handle = self._handles.get(k)
            if handle is None:
                new_items.append((k, v))
                new_priorities.append(priority)
            else:
                old_handles.append(handle)
                old_values.append(v)
                old_priorities.append(priority)

        new_handles = self._heap.push_many(new_priorities)
        try:
            self._heap.update_many(old_handles, old_priorities)
        except BaseException:
            for handle in new_handles:
                self._heap.remove(handle)
            raise
        for handle, (k, v) in zip(new_handles, new_items):
            self._bind(handle, k, v)
        for handle, v in zip(old_handles, old_values):
            self._values[handle] = v

    def _load(self, items: dict[K, V]) -> None:
        """Bulk-load into an empty queue without a per-item Python loop.

        After ``clear()`` the heap issues handles ``0..n-1`` in input order, so
        handles coincide with list positions.
        """
        keys, values = list(items), list(items.values())
        priorities = values if self._keyfn is None else list(map(self._keyfn, values))
        self._heap.clear()
        self._heap.push_many(priorities)
        self._handles = dict(zip(keys, range(len(keys))))
        self._keys, self._values = keys, values

    def clear(self) -> None:
        self._heap.clear()
        self._handles.clear()
        self._keys.clear()
        self._values.clear()

    def copy(self) -> PQDict[K, V]:
        """Shallow copy; the native heap is cloned without re-heapifying."""
        new = type(self).__new__(type(self))
        new._heap = self._heap.copy()
        new._handles = self._handles.copy()
        new._keys = self._keys.copy()
        new._values = self._values.copy()
        new._keyfn = self._keyfn
        return new

    __copy__ = copy

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild, (type(self), list(self.items()), self._keyfn, self.reverse))

    def __repr__(self) -> str:
        items = ", ".join(f"{k!r}: {v!r}" for k, v in self.items())
        extra = "" if self._keyfn is None else f", key={self._keyfn!r}"
        extra += ", reverse=True" if self.reverse else ""
        return f"{type(self).__name__}({{{items}}}{extra})"

    # -- priority queue API ------------------------------------------------

    def pop(self, key: Any = _MISSING, default: Any = _MISSING) -> Any:
        """Without ``key``: remove the top item and return its key.
        With ``key``: remove that item and return its value (dict semantics).
        """
        if key is _MISSING:
            if not self._handles and default is not _MISSING:
                return default
            return self.popitem()[0]
        handle = self._handles.get(key)
        if handle is None:
            if default is _MISSING:
                raise KeyError(key)
            return default
        self._heap.remove(handle)
        return self._unbind(handle)[1]

    def popvalue(self, default: Any = _MISSING) -> Any:
        """Remove the top item and return its value."""
        if not self._handles and default is not _MISSING:
            return default
        return self.popitem()[1]

    def popitem(self) -> tuple[K, V]:
        """Remove and return the top ``(key, value)`` pair."""
        try:
            handle = self._heap.pop()[0]
        except IndexError:
            raise KeyError("PQDict is empty") from None
        keys, values = self._keys, self._values
        key, value = keys[handle], values[handle]
        keys[handle] = values[handle] = None
        del self._handles[key]
        return key, value


    def top(self, default: Any = _MISSING) -> Any:
        """Return the key of the top item without removing it."""
        handle = self._top_handle()
        if handle is None:
            if default is _MISSING:
                raise KeyError("PQDict is empty")
            return default
        return self._keys[handle]

    def topvalue(self, default: Any = _MISSING) -> Any:
        """Return the value of the top item without removing it."""
        handle = self._top_handle()
        if handle is None:
            if default is _MISSING:
                raise KeyError("PQDict is empty")
            return default
        return self._values[handle]

    def topitem(self) -> tuple[K, V]:
        """Return the top ``(key, value)`` pair without removing it."""
        handle = self._top_handle()
        if handle is None:
            raise KeyError("PQDict is empty")
        return self._keys[handle], self._values[handle]

    def additem(self, key: K, value: V) -> None:
        """Add a new item. Raises ``KeyError`` if ``key`` is already present."""
        if key in self._handles:
            raise KeyError(f"{key!r} is already in the queue")
        self[key] = value

    def updateitem(self, key: K, new_val: V) -> None:
        """Update an existing item. Raises ``KeyError`` if ``key`` is missing."""
        if key not in self._handles:
            raise KeyError(key)
        self[key] = new_val

    def pushpopitem(self, key: K, value: V) -> tuple[K, V]:
        """Push an item then pop and return the top one, in a single sift.

        Raises ``KeyError`` if ``key`` is already present.
        """
        if key in self._handles:
            raise KeyError(f"{key!r} is already in the queue")
        popped = self._heap.push_pop(self._priority(value))
        if popped is None:
            return key, value
        handle = popped[0]
        old = self._unbind(handle)
        self._bind(handle, key, value)
        return old

    def replace_key(self, key: K, new_key: K) -> None:
        """Rename ``key`` to ``new_key`` in place, keeping value and priority."""
        if new_key in self._handles:
            raise KeyError(f"{new_key!r} is already in the queue")
        handle = self._handles.pop(key)
        self._handles[new_key] = handle
        self._keys[handle] = new_key

    def swap_priority(self, key1: K, key2: K) -> None:
        """Exchange the values (and hence priorities) of two items."""
        h1, h2 = self._handles[key1], self._handles[key2]
        self._handles[key1], self._handles[key2] = h2, h1
        self._keys[h1], self._keys[h2] = key2, key1

    def popkeys(self) -> Iterator[K]:
        """Destructively iterate over keys in priority order."""
        while self._handles:
            yield self.popitem()[0]

    def popvalues(self) -> Iterator[V]:
        """Destructively iterate over values in priority order."""
        while self._handles:
            yield self.popitem()[1]

    def popitems(self) -> Iterator[tuple[K, V]]:
        """Destructively iterate over ``(key, value)`` pairs in priority order."""
        while self._handles:
            yield self.popitem()

    def heapify(self, key: Any = _MISSING) -> None:
        """Recompute priorities after values were mutated in place.

        With ``key``, only that item is re-prioritized (``O(log n)``);
        otherwise all items are (``O(n)``).
        """
        if key is not _MISSING:
            handle = self._handles[key]
            self._heap.update(handle, self._priority(self._values[handle]))
            return
        handles = list(self._handles.values())
        self._heap.update_many(handles, [self._priority(self._values[h]) for h in handles])


def _rebuild(
    cls: type[PQDict[Any, Any]],
    items: list[tuple[Any, Any]],
    keyfn: Callable[[Any], float] | None,
    reverse: bool,
) -> PQDict[Any, Any]:
    return cls(items, key=keyfn, reverse=reverse)


def minpq(*args: Any, **kwargs: Any) -> PQDict[Any, Any]:
    """Min-priority queue from ``dict(*args, **kwargs)``."""
    return PQDict(dict(*args, **kwargs))


def maxpq(*args: Any, **kwargs: Any) -> PQDict[Any, Any]:
    """Max-priority queue from ``dict(*args, **kwargs)``."""
    return PQDict(dict(*args, **kwargs), reverse=True)


def _select(n: int, mapping: Mapping[K, Any], key: Callable[[Any], float] | None, reverse: bool) -> list[K]:
    keys = list(mapping)
    values = mapping.values() if key is None else map(key, mapping.values())
    heap = IndexedHeap(reverse=reverse, capacity=len(keys))
    by_handle: list[Any] = [None] * len(keys)
    for k, handle in zip(keys, heap.push_many(list(values))):
        by_handle[handle] = k
    return [by_handle[handle] for handle, _ in heap.pop_many(n)]


def nsmallest(n: int, mapping: Mapping[K, Any], key: Callable[[Any], float] | None = None) -> list[K]:
    """Keys of the ``n`` items with the smallest priorities, smallest first."""
    return _select(n, mapping, key, reverse=False)


def nlargest(n: int, mapping: Mapping[K, Any], key: Callable[[Any], float] | None = None) -> list[K]:
    """Keys of the ``n`` items with the largest priorities, largest first."""
    return _select(n, mapping, key, reverse=True)
