"""The native and pure-Python cores must be indistinguishable through the API."""

import math
import random

import pytest

from iheap import _pyheap

native = pytest.importorskip("iheap._iheap", reason="native core not available")


def outcome(fn, *args):
    try:
        return ("ok", fn(*args))
    except Exception as exc:  # noqa: BLE001 - the exception type is the result
        return ("error", type(exc).__name__)


@pytest.mark.parametrize("reverse", [False, True])
def test_random_operations_match(reverse):
    rng = random.Random(7 + reverse)
    a, b = native.IndexedHeap(reverse=reverse), _pyheap.IndexedHeap(reverse=reverse)
    live: list[int] = []
    for _ in range(4000):
        p = rng.random() * 100 - 50  # distinct priorities: no tie ambiguity
        op = rng.randrange(12)
        if op <= 2:
            call = ("push", p)
        elif op == 3:
            call = ("push_many", [rng.random() for _ in range(rng.randrange(20))])
        elif op == 4:
            call = ("pop",)
        elif op == 5:
            call = ("pop_many", rng.randrange(5))
        elif op == 6:
            call = ("push_pop", p)
        elif op == 7:
            call = ("update", rng.choice(live) if live else 0, p)
        elif op == 8:
            call = ("remove", rng.choice(live) if live else 0)
        elif op == 9:
            hs = rng.sample(live, min(len(live), 3))
            call = ("update_many", hs, [rng.random() for _ in hs])
        elif op == 10:
            call = rng.choice([("push", math.nan), ("push", "x"), ("update", 10**6, 1.0),
                               ("update_many", [0], []), ("priority", 10**6)])
        else:
            call = rng.choice([("peek",), ("items",), ("clear",) if rng.random() < 0.05 else ("peek",)])
        ra = outcome(getattr(a, call[0]), *call[1:])
        rb = outcome(getattr(b, call[0]), *call[1:])
        assert ra == rb, (call, ra, rb)
        assert (len(a), a.handle_bound, bool(a)) == (len(b), b.handle_bound, bool(b))
        live = [h for h, _ in a.items()]
    assert a.items() == b.items()
    assert a.pop_many(len(a)) == b.pop_many(len(b))


@pytest.mark.parametrize("make", [lambda: native.IndexedHeap(), lambda: _pyheap.IndexedHeap()])
def test_argument_rules_match(make):
    heap = make()
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
    assert type(make()).__name__ == "IndexedHeap"


@pytest.mark.parametrize("cls", [native.IndexedHeap, _pyheap.IndexedHeap])
def test_constructor_rules_match(cls):
    assert cls(reverse=1).reverse and not cls(reverse=0).reverse
    for bad in (-1, 2**40):
        with pytest.raises(ValueError):
            cls(capacity=bad)
