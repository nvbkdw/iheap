"""``pqdict``-compatible priority queue dictionary on top of ``IndexedHeap``.

Storage layout: the native heap owns ``(priority, handle)`` pairs only. This
module owns everything else:

* ``_handles`` maps each key to its heap handle;
* ``_keys[h]`` / ``_values[h]`` hold the key and value of handle ``h``.

Handles are dense and recycled by the heap, so the two lists never grow beyond
the peak queue size.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, MutableMapping
from typing import Any, Generic, Literal, TypeVar

from ._core import IndexedHeap
from ._objheap import ObjectHeap

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING: Any = object()

#: Priority function: maps an item's key and value to its priority.
PriorityFn = Callable[[K, V], object]

#: ``"float"``: numeric priorities on the native core. ``"object"``: any
#: mutually comparable priorities (str, tuple, ...) on the generic core.
PriorityType = Literal["float", "object"]


def _new_heap(priority_type: PriorityType, reverse: bool, capacity: int = 0) -> Any:
    if priority_type == "float":
        return IndexedHeap(reverse=reverse, capacity=capacity)
    if priority_type == "object":
        return ObjectHeap(reverse=reverse)
    raise ValueError(f"priority_type must be 'float' or 'object', not {priority_type!r}")


class PQDict(MutableMapping[K, V], Generic[K, V]):
    """A mapping of keys to values that also behaves as a priority queue.

    The priority of an item is ``key(item_key, value)`` if a ``key`` function
    is given, otherwise the value itself. (Unlike ``pqdict``, the function
    receives the item's key as well as its value.) With the default
    ``priority_type="float"``, priorities must be numbers (anything ``float``
    accepts except ``str``) and run on the fast native core. With
    ``priority_type="object"``, they may be any mutually comparable objects,
    such as strings, tuples or dates, ordered with ``<`` at pure-Python speed.
    NaN is rejected either way.
    By default the item with the smallest priority is on top; pass
    ``reverse=True`` for a max-queue. Ties are popped in unspecified order.

    Supports every ``dict`` operation (``get``, ``setdefault``, ``|``, ``|=``,
    ``reversed``, views, equality, pickling, ...). Like ``dict``, iteration
    follows key insertion order; use [`popkeys`][iheap.PQDict.popkeys],
    [`popitems`][iheap.PQDict.popitems] etc. for priority order. Unlike
    ``dict``, [`popitem`][iheap.PQDict.popitem] removes the top-priority item
    rather than the last inserted one.

    Only priorities are sent to the native heap; keys and values stay in
    Python.

    Not safe for concurrent mutation: guard a queue that several threads
    modify with a lock (required on free-threaded Python).

    Args:
        data: Initial items, as a mapping or an iterable of ``(key, value)``
            pairs. Built in ``O(n)`` with a single native call.
        key: Function ``(item_key, value) -> priority``. Defaults to using
            the value itself as the priority.
        reverse: If true, the largest priority is on top (max-queue).
        priority_type: ``"float"`` (default) for numeric priorities on the
            native core, or ``"object"`` for any comparable priorities.

    Raises:
        TypeError: A priority is not a number (``"float"``), or cannot be
            compared with the others (``"object"``).
        ValueError: A priority is NaN, or ``priority_type`` is unknown.

    Examples:
        >>> pq = PQDict({"a": 3, "b": 1}, reverse=True)
        >>> pq["c"] = 5
        >>> pq.top()
        'c'
        >>> list(pq.popitems())
        [('c', 5), ('a', 3), ('b', 1)]
        >>> by_len = PQDict({"pear": 1, "fig": 9}, key=lambda k, v: len(k) + v)
        >>> by_len.top()
        'pear'
        >>> words = PQDict({"x": "pear", "y": "apple"}, priority_type="object")
        >>> words.popitem()
        ('y', 'apple')
    """

    __slots__ = ("_heap", "_handles", "_keys", "_values", "_keyfn", "_priority_type")

    def __init__(
        self,
        data: Mapping[K, V] | Iterable[tuple[K, V]] | None = None,
        *,
        key: PriorityFn[K, V] | None = None,
        reverse: bool = False,
        priority_type: PriorityType = "float",
    ) -> None:
        self._heap = _new_heap(priority_type, reverse)
        self._priority_type = priority_type
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
        value: V = None,  # type: ignore[assignment]
        *,
        key: PriorityFn[K, V] | None = None,
        reverse: bool = False,
        priority_type: PriorityType = "float",
    ) -> PQDict[K, V]:
        """Create a queue with keys from ``iterable``, all set to ``value``.

        Args:
            iterable: Keys to insert.
            value: Value (and, without ``key``, priority) shared by every key.
            key: Priority function, as in the constructor.
            reverse: Max-queue if true, as in the constructor.
            priority_type: As in the constructor.
        """
        return cls(
            ((k, value) for k in iterable), key=key, reverse=reverse, priority_type=priority_type
        )

    @property
    def keyfn(self) -> PriorityFn[K, V] | None:
        """The priority function, or ``None`` if values are priorities."""
        return self._keyfn

    @property
    def reverse(self) -> bool:
        """``True`` for a max-queue."""
        return self._heap.reverse

    @property
    def priority_type(self) -> PriorityType:
        """``"float"`` (native core) or ``"object"`` (generic core)."""
        return self._priority_type

    # -- internal helpers --------------------------------------------------

    def _priority(self, key: K, value: V) -> Any:
        return value if self._keyfn is None else self._keyfn(key, value)

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

    def __reversed__(self) -> Iterator[K]:
        return reversed(self._handles)

    def get(self, key: K, default: Any = None) -> Any:
        """Return the value for ``key`` if present, else ``default``."""
        handle = self._handles.get(key)
        return default if handle is None else self._values[handle]

    def setdefault(self, key: K, default: Any = None) -> Any:
        """Return the value for ``key``, inserting ``default`` first if absent."""
        handle = self._handles.get(key)
        if handle is not None:
            return self._values[handle]
        self[key] = default
        return default

    def __or__(self, other: Any) -> PQDict[K, V]:
        if not isinstance(other, Mapping):
            return NotImplemented
        new = self.copy()
        new.update(other)
        return new

    def __ror__(self, other: Any) -> PQDict[K, V]:
        if not isinstance(other, Mapping):
            return NotImplemented
        new = type(self)(
            other, key=self._keyfn, reverse=self.reverse, priority_type=self._priority_type
        )
        new.update(self)
        return new

    def __ior__(self, other: Any) -> PQDict[K, V]:
        self.update(other)
        return self

    # The hot paths below inline ``_priority``/``_bind``/``_unbind``: a Python
    # call frame costs more than the native heap operation itself.

    def __setitem__(self, key: K, value: V) -> None:
        keyfn = self._keyfn
        priority = value if keyfn is None else keyfn(key, value)
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
        """Insert or update many items, like ``dict.update``.

        New keys are bulk-inserted (``O(n)`` heapify when large) and existing
        keys bulk-updated, with one native call each. The operation is atomic:
        if any priority is invalid, nothing changes.

        Args:
            other: A mapping or an iterable of ``(key, value)`` pairs.
            **kwargs: Additional items.
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
            priority = self._priority(k, v)
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
        priorities = values if self._keyfn is None else list(map(self._keyfn, keys, values))
        self._heap.clear()
        self._heap.push_many(priorities)
        self._handles.update(zip(keys, range(len(keys))))
        self._keys, self._values = keys, values

    def clear(self) -> None:
        """Remove all items."""
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
        new._priority_type = self._priority_type
        return new

    __copy__ = copy

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            _rebuild,
            (type(self), list(self.items()), self._keyfn, self.reverse, self._priority_type),
        )

    def __repr__(self) -> str:
        items = ", ".join(f"{k!r}: {v!r}" for k, v in self.items())
        extra = "" if self._keyfn is None else f", key={self._keyfn!r}"
        extra += ", reverse=True" if self.reverse else ""
        extra += ", priority_type='object'" if self._priority_type == "object" else ""
        return f"{type(self).__name__}({{{items}}}{extra})"

    # -- priority queue API ------------------------------------------------

    def pop(self, key: Any = _MISSING, default: Any = _MISSING) -> Any:
        """Remove an item: the top one, or ``key`` with ``dict`` semantics.

        Args:
            key: If given, remove this item and return its value. If omitted,
                remove the top item and return its *key*.
            default: Returned instead of raising when the item is missing (or
                the queue is empty).

        Raises:
            KeyError: ``key`` is missing, or the queue is empty, and no
                ``default`` was given.
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
        """Remove the top item and return its value.

        Raises:
            KeyError: The queue is empty and no ``default`` was given.
        """
        if not self._handles and default is not _MISSING:
            return default
        return self.popitem()[1]

    def popitem(self) -> tuple[K, V]:
        """Remove and return the top ``(key, value)`` pair.

        Raises:
            KeyError: The queue is empty.
        """
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
        """Return the key of the top item without removing it.

        Raises:
            KeyError: The queue is empty and no ``default`` was given.
        """
        handle = self._top_handle()
        if handle is None:
            if default is _MISSING:
                raise KeyError("PQDict is empty")
            return default
        return self._keys[handle]

    def topvalue(self, default: Any = _MISSING) -> Any:
        """Return the value of the top item without removing it.

        Raises:
            KeyError: The queue is empty and no ``default`` was given.
        """
        handle = self._top_handle()
        if handle is None:
            if default is _MISSING:
                raise KeyError("PQDict is empty")
            return default
        return self._values[handle]

    def topitem(self) -> tuple[K, V]:
        """Return the top ``(key, value)`` pair without removing it.

        Raises:
            KeyError: The queue is empty.
        """
        handle = self._top_handle()
        if handle is None:
            raise KeyError("PQDict is empty")
        return self._keys[handle], self._values[handle]

    def additem(self, key: K, value: V) -> None:
        """Add a new item.

        Raises:
            KeyError: ``key`` is already present.
        """
        if key in self._handles:
            raise KeyError(f"{key!r} is already in the queue")
        self[key] = value

    def updateitem(self, key: K, new_val: V) -> None:
        """Update the value (and priority) of an existing item.

        Raises:
            KeyError: ``key`` is missing.
        """
        if key not in self._handles:
            raise KeyError(key)
        self[key] = new_val

    def pushpopitem(self, key: K, value: V) -> tuple[K, V]:
        """Push an item, then pop and return the top one, in a single pass.

        Faster than ``additem`` followed by ``popitem``. If the new item would
        be on top, it is returned straight away and the queue is unchanged.

        Raises:
            KeyError: ``key`` is already present.
        """
        if key in self._handles:
            raise KeyError(f"{key!r} is already in the queue")
        popped = self._heap.push_pop(self._priority(key, value))
        if popped is None:
            return key, value
        handle = popped[0]
        old = self._unbind(handle)
        self._bind(handle, key, value)
        return old

    def replace_key(self, key: K, new_key: K) -> None:
        """Rename ``key`` to ``new_key``, keeping its value.

        Without a ``key`` function the priority is unchanged (``O(1)``);
        with one, it is recomputed as ``key(new_key, value)``.

        Raises:
            KeyError: ``key`` is missing or ``new_key`` is already present.
        """
        if new_key in self._handles:
            raise KeyError(f"{new_key!r} is already in the queue")
        handle = self._handles[key]
        if self._keyfn is not None:
            # The native update validates before mutating: atomic.
            self._heap.update(handle, self._keyfn(new_key, self._values[handle]))
        del self._handles[key]
        self._handles[new_key] = handle
        self._keys[handle] = new_key

    def swap_priority(self, key1: K, key2: K) -> None:
        """Exchange the values of two items, and hence their priorities.

        Without a ``key`` function this is ``O(1)``; with one, both
        priorities are recomputed from the new ``(key, value)`` pairs.

        Raises:
            KeyError: Either key is missing.
        """
        h1, h2 = self._handles[key1], self._handles[key2]
        if self._keyfn is not None:
            # key1 takes over h2 (and its value), key2 takes over h1. The
            # native batch update validates before mutating: atomic.
            p1 = self._keyfn(key1, self._values[h2])
            p2 = self._keyfn(key2, self._values[h1])
            self._heap.update_many([h2, h1], [p1, p2])
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

        Only needed with a ``key`` function whose result depends on mutable
        state of the value.

        Args:
            key: Re-prioritize only this item (``O(log n)``). If omitted, all
                items are re-prioritized (``O(n)``).

        Raises:
            KeyError: ``key`` is missing.
        """
        if key is not _MISSING:
            handle = self._handles[key]
            self._heap.update(handle, self._priority(key, self._values[handle]))
            return
        handles = list(self._handles.values())
        self._heap.update_many(
            handles, [self._priority(self._keys[h], self._values[h]) for h in handles]
        )


def _rebuild(
    cls: type[PQDict[Any, Any]],
    items: list[tuple[Any, Any]],
    keyfn: PriorityFn[Any, Any] | None,
    reverse: bool,
    priority_type: PriorityType = "float",
) -> PQDict[Any, Any]:
    return cls(items, key=keyfn, reverse=reverse, priority_type=priority_type)


def minpq(*args: Any, **kwargs: Any) -> PQDict[Any, Any]:
    """Create a min-priority queue from ``dict(*args, **kwargs)``.

    Examples:
        >>> minpq(a=3, b=1).top()
        'b'
    """
    return PQDict(dict(*args, **kwargs))


def maxpq(*args: Any, **kwargs: Any) -> PQDict[Any, Any]:
    """Create a max-priority queue from ``dict(*args, **kwargs)``.

    Examples:
        >>> maxpq(a=3, b=1).top()
        'a'
    """
    return PQDict(dict(*args, **kwargs), reverse=True)


def _select(
    n: int,
    mapping: Mapping[K, V],
    key: PriorityFn[K, V] | None,
    reverse: bool,
    priority_type: PriorityType,
) -> list[K]:
    keys = list(mapping)
    values = mapping.values() if key is None else map(key, keys, mapping.values())
    heap = _new_heap(priority_type, reverse, capacity=len(keys))
    by_handle: list[Any] = [None] * len(keys)
    for k, handle in zip(keys, heap.push_many(list(values))):
        by_handle[handle] = k
    return [by_handle[handle] for handle, _ in heap.pop_many(n)]


def nsmallest(
    n: int,
    mapping: Mapping[K, V],
    key: PriorityFn[K, V] | None = None,
    priority_type: PriorityType = "float",
) -> list[K]:
    """Return the keys of the ``n`` items with the smallest priorities.

    Runs in ``O(N + n log N)`` in native code.

    Args:
        n: Number of keys to return.
        mapping: Any mapping of keys to values.
        key: Function ``(item_key, value) -> priority``. Defaults to the value.
        priority_type: ``"float"`` (native core) or ``"object"`` (any
            comparable priorities), as in [`PQDict`][iheap.PQDict].

    Returns:
        Up to ``n`` keys, smallest priority first.
    """
    return _select(n, mapping, key, reverse=False, priority_type=priority_type)


def nlargest(
    n: int,
    mapping: Mapping[K, V],
    key: PriorityFn[K, V] | None = None,
    priority_type: PriorityType = "float",
) -> list[K]:
    """Return the keys of the ``n`` items with the largest priorities.

    Args:
        n: Number of keys to return.
        mapping: Any mapping of keys to values.
        key: Function ``(item_key, value) -> priority``. Defaults to the value.
        priority_type: ``"float"`` (native core) or ``"object"`` (any
            comparable priorities), as in [`PQDict`][iheap.PQDict].

    Returns:
        Up to ``n`` keys, largest priority first.
    """
    return _select(n, mapping, key, reverse=True, priority_type=priority_type)
