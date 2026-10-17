import copy
import math
import random

import pytest

from iheap import IndexedHeap


def drain(heap):
    return [p for _, p in heap.pop_many(len(heap))]


def test_push_pop_order():
    heap = IndexedHeap()
    for p in [5, 1, 4.5, -2, 9]:
        heap.push(p)
    assert drain(heap) == [-2, 1, 4.5, 5, 9]
    with pytest.raises(IndexError):
        heap.pop()
    assert heap.peek() is None


def test_reverse():
    heap = IndexedHeap(reverse=True)
    assert heap.reverse
    heap.push_many([1, 3, 2, math.inf])
    assert drain(heap) == [math.inf, 3, 2, 1]


def test_handles():
    heap = IndexedHeap()
    a, b = heap.push(1), heap.push(2)
    assert a in heap and b in heap and -1 not in heap and 99 not in heap
    assert heap.remove(a) == 1
    assert a not in heap
    with pytest.raises(KeyError):
        heap.remove(a)
    with pytest.raises(KeyError):
        heap.priority(a)
    assert heap.push(7) == a  # recycled
    assert heap.handle_bound == 2
    assert heap.update(b, 10) == 2
    assert heap.priority(b) == 10
    assert heap.peek() == (a, 7)


def test_errors_are_atomic():
    heap = IndexedHeap()
    h = heap.push(1)
    with pytest.raises(ValueError):
        heap.push(math.nan)
    with pytest.raises(ValueError):
        heap.push_many([1, math.nan])
    with pytest.raises(ValueError):
        heap.update_many([h], [1, 2])
    with pytest.raises(KeyError):
        heap.update_many([h, 42], [0, 0])
    with pytest.raises(TypeError):
        heap.push("x")
    assert len(heap) == 1 and heap.priority(h) == 1


def test_push_pop():
    heap = IndexedHeap()
    assert heap.push_pop(3) is None
    a = heap.push(1)
    heap.push(2)
    assert heap.push_pop(1) is None
    assert heap.push_pop(5) == (a, 1)
    assert heap.priority(a) == 5


def test_copy_is_independent():
    heap = IndexedHeap()
    heap.push_many([3, 1, 2])
    for clone in (heap.copy(), copy.copy(heap), copy.deepcopy(heap)):
        clone.pop()
        assert len(heap) == 3


def test_random_against_model():
    rng = random.Random(0)
    for reverse in (False, True):
        heap, model = IndexedHeap(reverse=reverse), {}
        for _ in range(5000):
            op = rng.random()
            if op < 0.35 or not model:
                p = rng.randint(-20, 20)
                model[heap.push(p)] = p
            elif op < 0.5:
                h = rng.choice(list(model))
                assert heap.remove(h) == model.pop(h)
            elif op < 0.65:
                h, p = heap.pop()
                assert model.pop(h) == p
                assert all((q <= p) if reverse else (q >= p) for q in model.values())
            else:
                h = rng.choice(list(model))
                model[h] = rng.uniform(-20, 20)
                heap.update(h, model[h])
        assert dict(heap.items()) == model
        assert drain(heap) == sorted(model.values(), reverse=reverse)
