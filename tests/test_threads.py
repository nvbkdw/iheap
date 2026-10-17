"""Concurrency: one IndexedHeap shared by many threads.

Meaningful on free-threaded Python (no GIL), where calls truly overlap; on a
regular build it still checks that thread switches never corrupt the heap.
"""

import sys
import threading

import pytest

import iheap
from iheap import IndexedHeap

THREADS = 8
OPS = 5_000


def test_import_keeps_gil_disabled():
    if not hasattr(sys, "_is_gil_enabled") or "free-threading" not in sys.version:
        pytest.skip("not a free-threaded interpreter")
    assert iheap.IndexedHeap is not None
    assert not sys._is_gil_enabled(), "importing iheap re-enabled the GIL"


def test_concurrent_mutation_is_serialized():
    heap = IndexedHeap()
    start = threading.Barrier(THREADS)
    errors: list[BaseException] = []
    popped: list[list[float]] = [[] for _ in range(THREADS)]

    def worker(t: int) -> None:
        try:
            start.wait()
            mine = [heap.push(float(t * OPS + i)) for i in range(OPS)]
            for h in mine[::2]:
                heap.update(h, -1.0)  # this thread's handles: still live
            for h in mine[1::4]:
                heap.remove(h)
            for _ in range(OPS // 4):
                popped[t].append(heap.pop()[1])
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors, errors
    pushed, removed, pops = THREADS * OPS, THREADS * len(range(1, OPS, 4)), THREADS * (OPS // 4)
    assert len(heap) == pushed - removed - pops
    for seq in popped:
        assert seq == sorted(seq), "a thread saw pops out of priority order"
    rest = [p for _, p in heap.pop_many(len(heap))]
    assert rest == sorted(rest)
