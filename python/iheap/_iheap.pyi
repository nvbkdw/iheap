from collections.abc import Iterable
from typing import Any, Final, SupportsIndex, final

#: Largest number of handles a heap can issue.
MAX_LEN: Final[int]

@final
class IndexedHeap:
    """Native indexed priority queue of float priorities addressed by handles.

    [`push`][iheap.IndexedHeap.push] returns an integer *handle* that stays
    valid until its entry is popped or removed, and lets you update or remove
    that entry in ``O(log n)``. Handles are small non-negative integers below
    [`handle_bound`][iheap.IndexedHeap.handle_bound] and are recycled, so you
    can keep per-entry data in a plain list indexed by handle. The heap
    itself holds no Python objects.

    Priorities must be convertible to ``float`` and not NaN. Integers are
    exact up to 2**53. Handles may be any int-like (``SupportsIndex``); one
    that names no live entry, including negative or huge values, raises
    ``KeyError``. Batch methods accept any iterable. Heaps pickle and copy
    exactly, including which handles will be reused next.

    Thread safety: a heap may be shared between threads, including on
    free-threaded Python. Each call runs under an internal lock, so
    concurrent calls are serialized; sequences of calls are not atomic.

    Args:
        reverse: If true, the largest priority is on top (max-heap).
        capacity: Number of entries to pre-allocate room for.

    Raises:
        ValueError: ``capacity`` is negative or above ``MAX_LEN``.
        MemoryError: ``capacity`` entries cannot be allocated.

    Examples:
        >>> heap = IndexedHeap()
        >>> a, b = heap.push(3.0), heap.push(1.0)
        >>> heap.peek() == (b, 1.0)
        True
        >>> heap.update(a, 0.5)
        3.0
        >>> heap.pop() == (a, 0.5)
        True
    """

    def __init__(self, *, reverse: object = False, capacity: SupportsIndex = 0) -> None: ...
    @property
    def reverse(self) -> bool:
        """``True`` for a max-heap."""
    @property
    def handle_bound(self) -> int:
        """Exclusive upper bound of every live handle (the handle high-water mark)."""
    def __len__(self) -> int:
        """Number of live entries."""
    def __bool__(self) -> bool: ...
    def __contains__(self, handle: SupportsIndex) -> bool:
        """Whether ``handle`` refers to a live entry."""
    def push(self, priority: float) -> int:
        """Insert an entry in ``O(log n)``.

        Args:
            priority: Priority of the new entry.

        Returns:
            The handle of the new entry.

        Raises:
            ValueError: ``priority`` is NaN.
            TypeError: ``priority`` is not convertible to ``float``.
        """
    def push_many(self, priorities: Iterable[float]) -> list[int]:
        """Insert many entries at once.

        Large batches are appended and re-heapified in ``O(n)``. Atomic: if any
        priority is invalid, nothing is inserted. On an empty heap after
        construction or [`clear`][iheap.IndexedHeap.clear], the returned
        handles are ``0, 1, ..., len(priorities) - 1``.

        Args:
            priorities: Priorities of the new entries.

        Returns:
            Handles of the new entries, in input order.

        Raises:
            ValueError: A priority is NaN.
        """
    def peek(self) -> tuple[int, float] | None:
        """Return ``(handle, priority)`` of the top entry, or ``None`` if empty."""
    def pop(self) -> tuple[int, float]:
        """Remove and return the top entry in ``O(log n)``.

        Returns:
            ``(handle, priority)`` of the removed entry. The handle becomes
            invalid and may be reused by later pushes.

        Raises:
            IndexError: The heap is empty.
        """
    def push_pop(self, priority: float) -> tuple[int, float] | None:
        """Fused push-then-pop in a single pass (like ``heapq.heappushpop``).

        Args:
            priority: Priority of the new entry.

        Returns:
            ``None`` if the new entry would be popped right away (the heap is
            empty or its top does not strictly precede ``priority``); the heap
            is then unchanged. Otherwise the top entry is popped, **its handle
            is reassigned to the new entry**, and ``(handle, popped_priority)``
            is returned.

        Raises:
            ValueError: ``priority`` is NaN.
        """
    def pop_many(self, n: SupportsIndex) -> list[tuple[int, float]]:
        """Pop up to ``n`` entries (none if ``n <= 0``).

        Returns:
            ``(handle, priority)`` pairs in priority order.
        """
    def priority(self, handle: SupportsIndex) -> float:
        """Return the priority of ``handle``.

        Raises:
            KeyError: ``handle`` is not live.
        """
    def update(self, handle: SupportsIndex, priority: float) -> float:
        """Change the priority of ``handle`` in ``O(log n)``.

        Returns:
            The previous priority.

        Raises:
            KeyError: ``handle`` is not live.
            ValueError: ``priority`` is NaN.
        """
    def update_many(self, handles: Iterable[SupportsIndex], priorities: Iterable[float]) -> None:
        """Change many priorities at once.

        Re-heapifies in ``O(n)`` when that is cheaper than individual updates.
        Atomic: on error nothing changes. If a handle appears more than once,
        the last priority wins.

        Raises:
            KeyError: A handle is not live.
            ValueError: The lists differ in length, or a priority is NaN.
        """
    def remove(self, handle: SupportsIndex) -> float:
        """Remove ``handle`` in ``O(log n)``.

        Returns:
            The priority of the removed entry.

        Raises:
            KeyError: ``handle`` is not live.
        """
    def clear(self) -> None:
        """Remove every entry. Handle numbering restarts at 0."""
    def items(self) -> list[tuple[int, float]]:
        """Return live ``(handle, priority)`` pairs in handle order (not priority order)."""
    def copy(self) -> IndexedHeap:
        """Return an independent copy, handles included."""
    def __copy__(self) -> IndexedHeap: ...
    def __deepcopy__(self, memo: Any) -> IndexedHeap: ...
    def __reduce__(self) -> tuple[Any, ...]: ...
