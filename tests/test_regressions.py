"""Regression tests for issues found in the code review."""

import math
import pickle

import pytest

from iheap import ObjectHeap, PQDict
from iheap._core import PythonIndexedHeap, heap_class

BACKENDS = ["python"] + (["native"] if heap_class("auto") is not PythonIndexedHeap else [])


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("priority_type", ["auto", "float", "object"])
def test_failed_update_leaves_queue_usable(backend, priority_type):
    # A failed update() used to corrupt the free list, breaking the next push.
    pq = PQDict({"a": 1, "b": 2}, backend=backend, priority_type=priority_type)
    with pytest.raises(ValueError):
        pq.update({"c": 3, "d": 4, "a": math.nan})
    assert pq == {"a": 1, "b": 2}
    for k, v in [("e", 5), ("f", 0), ("g", 7)]:
        pq[k] = v
    assert len(pq) == len(pq._heap) == 5
    assert list(pq.popkeys()) == ["f", "a", "b", "e", "g"]


@pytest.mark.parametrize(
    "op",
    [
        lambda h: h.push((1, 2)),
        lambda h: h.update(0, (1, 3)),
        lambda h: h.push_pop((1, 4)),
        lambda h: h.push_many([(5, "ok"), (1, 9)]),
    ],
)
def test_object_heap_is_atomic_when_comparisons_raise(op):
    heap = ObjectHeap()
    heap.push_many([(0, "x"), (1, "a"), (2, "b")])
    before = heap.items()
    with pytest.raises(TypeError):
        op(heap)
    assert heap.items() == before
    assert heap.pop() == (0, (0, "x"))  # still consistent


def test_object_heap_detects_reentrant_comparisons():
    class Reentrant:
        def __init__(self, heap):
            self.heap = heap

        def __lt__(self, other):
            self.heap.push(1)
            return False

        __ge__ = __lt__

        def __ne__(self, other):
            return False

    heap = ObjectHeap()
    heap.push(Reentrant(heap))
    with pytest.raises(RuntimeError):
        heap.push(Reentrant(heap))
    assert len(heap) == 1


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


@pytest.mark.parametrize("cls", [heap_class(b) for b in BACKENDS] + [ObjectHeap])
def test_heaps_pickle_exactly(cls):
    heap = cls(reverse=True)
    heap.push_many([3, 1, 2, 5])
    heap.remove(1)
    heap.pop()
    clone = pickle.loads(pickle.dumps(heap))
    assert type(clone) is cls and clone.reverse
    assert clone.items() == heap.items() and clone.handle_bound == heap.handle_bound
    assert clone.push(9) == heap.push(9)  # same handle reuse
    assert clone.pop_many(9) == heap.pop_many(9)
