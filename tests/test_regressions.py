"""Regression tests for issues found in code review, and the binding's
argument rules."""

import math
import pickle

import pytest

from iheap import MAX_LEN, IndexedHeap, PQDict


def test_failed_update_leaves_queue_usable():
    # A failed update() used to corrupt the free list, breaking the next push.
    pq = PQDict({"a": 1, "b": 2})
    with pytest.raises(ValueError):
        pq.update({"c": 3, "d": 4, "a": math.nan})
    assert pq == {"a": 1, "b": 2}
    for k, v in [("e", 5), ("f", 0), ("g", 7)]:
        pq[k] = v
    assert len(pq) == len(pq._heap) == 5
    assert list(pq.popkeys()) == ["f", "a", "b", "e", "g"]


def test_bulk_load_tolerates_reentrant_key_function():
    pq = PQDict()

    def key(k, v):
        if k == "a" and "zz" not in pq:
            pq["zz"] = -1  # runs before the bulk load decides the queue is empty
        return v

    pq._keyfn = key
    pq.update({"a": 1, "b": 2})
    assert pq == {"zz": -1, "a": 1, "b": 2}
    assert list(pq.popkeys()) == ["zz", "a", "b"]


def test_replace_key_with_itself_is_a_no_op():
    pq = PQDict({"a": 1})
    pq.replace_key("a", "a")
    assert pq == {"a": 1}
    with pytest.raises(KeyError):
        pq.replace_key("missing", "missing")


def test_heap_pickles_exactly():
    heap = IndexedHeap(reverse=True)
    heap.push_many([3, 1, 2, 5])
    heap.remove(1)
    heap.pop()
    clone = pickle.loads(pickle.dumps(heap))
    assert clone.reverse and clone.items() == heap.items()
    assert clone.handle_bound == heap.handle_bound
    assert clone.push(9) == heap.push(9)  # same handle reuse
    assert clone.pop_many(9) == heap.pop_many(9)


def test_handle_and_batch_argument_rules():
    heap = IndexedHeap()
    heap.push_many(x for x in [3.0, 1.0])  # any iterable
    heap.update_many(iter([0]), (p for p in [5.0]))
    assert heap.items() == [(0, 5.0), (1, 1.0)]
    for bad_handle in (-1, 2**70, 99):
        with pytest.raises(KeyError):
            heap.priority(bad_handle)
        assert bad_handle not in heap
    with pytest.raises(TypeError):
        heap.priority("0")
    assert heap.pop_many(-3) == []
    with pytest.raises(ValueError):
        heap.update(99, math.nan)  # priority errors take precedence


def test_constructor_argument_rules():
    assert IndexedHeap(reverse=1).reverse and not IndexedHeap(reverse=0).reverse
    for bad in (-1, MAX_LEN + 1):
        with pytest.raises(ValueError):
            IndexedHeap(capacity=bad)
