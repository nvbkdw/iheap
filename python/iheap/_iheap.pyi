from typing import Any, final

@final
class IndexedHeap:
    """Indexed priority queue of float priorities addressed by integer handles.

    ``push`` returns a handle that stays valid until the entry is popped or
    removed. Handles are small non-negative integers below ``handle_bound``
    and are recycled, so callers can keep per-entry data in a list indexed by
    handle. Priorities must be convertible to float and not NaN.
    """

    def __init__(self, *, reverse: bool = False, capacity: int = 0) -> None: ...
    @property
    def reverse(self) -> bool:
        """True for a max-heap."""
    @property
    def handle_bound(self) -> int:
        """Every live handle is strictly below this value."""
    def __len__(self) -> int: ...
    def __bool__(self) -> bool: ...
    def __contains__(self, handle: int) -> bool: ...
    def push(self, priority: float) -> int: ...
    def push_many(self, priorities: list[float]) -> list[int]:
        """Insert many priorities; returns their handles in input order."""
    def peek(self) -> tuple[int, float] | None:
        """``(handle, priority)`` of the top entry, or ``None`` if empty."""
    def pop(self) -> tuple[int, float]:
        """Remove and return the top entry. Raises ``IndexError`` if empty."""
    def push_pop(self, priority: float) -> tuple[int, float] | None:
        """Fused push-then-pop.

        Returns ``None`` if the new entry would be popped immediately (heap
        unchanged); otherwise pops the top entry, reassigns its handle to the
        new entry and returns ``(handle, popped_priority)``.
        """
    def pop_many(self, n: int) -> list[tuple[int, float]]:
        """Pop up to ``n`` entries in priority order."""
    def priority(self, handle: int) -> float: ...
    def update(self, handle: int, priority: float) -> float:
        """Set the priority of ``handle``; returns the previous priority."""
    def update_many(self, handles: list[int], priorities: list[float]) -> None:
        """Set many priorities at once (atomic; last write wins on duplicates)."""
    def remove(self, handle: int) -> float:
        """Remove ``handle``; returns its priority."""
    def clear(self) -> None:
        """Remove every entry. Handle numbering restarts at 0."""
    def items(self) -> list[tuple[int, float]]:
        """Live ``(handle, priority)`` pairs in internal heap order (not sorted)."""
    def copy(self) -> IndexedHeap: ...
    def __copy__(self) -> IndexedHeap: ...
    def __deepcopy__(self, memo: Any) -> IndexedHeap: ...
