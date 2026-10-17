"""ObjectHeap and PQDict(priority_type="object"): arbitrary comparable priorities."""

import copy
import math
import pickle
import random
from decimal import Decimal

import pytest

from iheap import ObjectHeap, PQDict, nlargest, nsmallest


@pytest.mark.parametrize("reverse", [False, True])
def test_random_string_operations_match_model(reverse):
    rng = random.Random(3 + reverse)
    words = [rng.choice("abc") * rng.randrange(1, 4) + rng.choice("xyz") for _ in range(30)]
    heap, model = ObjectHeap(reverse=reverse), {}
    best = max if reverse else min
    for _ in range(4000):
        op = rng.random()
        if op < 0.35 or not model:
            w = rng.choice(words)
            model[heap.push(w)] = w
        elif op < 0.5:
            h = rng.choice(list(model))
            assert heap.remove(h) == model.pop(h)
        elif op < 0.7:
            h, w = heap.pop()
            assert model.pop(h) == w
            assert w == best([w, *model.values()])
        else:
            h = rng.choice(list(model))
            model[h] = rng.choice(words)
            heap.update(h, model[h])
        if model:
            assert heap.peek()[1] == best(model.values())
    assert sorted(heap.items()) == sorted(model.items())
    rest = [w for _, w in heap.pop_many(len(heap))]
    assert rest == sorted(rest, reverse=reverse)


def test_exact_ordering_beyond_float_precision():
    big = 2**60
    heap = ObjectHeap()
    heap.push_many([big + 2, big, big + 1])  # equal as floats
    assert [p for _, p in heap.pop_many(3)] == [big, big + 1, big + 2]
    heap.push_many([Decimal("0.1") + Decimal("1e-30"), Decimal("0.1")])
    assert heap.pop()[1] == Decimal("0.1")


def test_tuples_break_ties_fifo():
    heap = ObjectHeap()
    for seq, prio in enumerate([2, 1, 2, 1]):
        heap.push((prio, seq))
    assert [p for _, p in heap.pop_many(4)] == [(1, 1), (1, 3), (2, 0), (2, 2)]


def test_incomparable_priorities_fail_atomically():
    heap = ObjectHeap()
    h = heap.push("a")
    heap.push("b")
    for bad in (lambda: heap.push(1), lambda: heap.push_many(["c", 2]),
                lambda: heap.update(h, 3), lambda: heap.update_many([h], [4]),
                lambda: heap.push_pop(5)):
        with pytest.raises(TypeError):
            bad()
    assert len(heap) == 2 and heap.items() == [(0, "a"), (1, "b")]
    empty = ObjectHeap()
    with pytest.raises(TypeError):
        empty.push_many(["x", 1])  # mixed batch into an empty heap
    assert len(empty) == 0 and empty.handle_bound == 0


def test_nan_like_priorities_are_rejected():
    heap = ObjectHeap()
    for nan in (math.nan, Decimal("NaN")):
        with pytest.raises(ValueError):
            heap.push(nan)
    assert len(heap) == 0


def test_copy_and_handles():
    heap = ObjectHeap(reverse=True)
    assert heap.push_many(["b", "c", "a"]) == [0, 1, 2]
    for clone in (heap.copy(), copy.copy(heap), copy.deepcopy(heap)):
        assert type(clone) is ObjectHeap and clone.reverse
        assert clone.pop() == (1, "c")
        assert len(heap) == 3
    assert repr(heap) == "ObjectHeap(len=3, reverse=True)"


def test_pqdict_object_priorities():
    pq = PQDict({"x": "pear", "y": "apple"}, priority_type="object")
    assert pq.priority_type == "object" and pq.top() == "y"
    pq["z"] = "aardvark"
    assert pq.top() == "z"
    assert pickle.loads(pickle.dumps(pq)) == pq
    clone = pickle.loads(pickle.dumps(pq))
    assert clone.priority_type == "object" and clone.top() == "z"
    assert (pq | {"w": "a"}).top() == "w" and (pq | {"w": "a"}).priority_type == "object"
    assert pq.copy().priority_type == "object"
    assert "priority_type='object'" in repr(pq)
    assert PQDict.fromkeys("ab", "v", priority_type="object").priority_type == "object"
    with pytest.raises(TypeError):
        pq["n"] = 1
    assert len(pq) == 3
    with pytest.raises(TypeError):
        PQDict({"a": "text"}, priority_type="float")  # pinned numeric core
    with pytest.raises(ValueError):
        PQDict(priority_type="decimal")  # type: ignore[arg-type]


def test_select_with_object_priorities():
    data = {"x": "b", "y": "a", "z": "c"}
    assert nsmallest(2, data, priority_type="object") == ["y", "x"]
    assert nlargest(1, data, priority_type="object") == ["z"]
