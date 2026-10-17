import copy
import math
import pickle
import random

import pytest

from iheap import PQDict, maxpq, minpq, nlargest, nsmallest


def test_mapping_basics():
    pq = PQDict({"a": 3, "b": 1, "c": 2})
    assert len(pq) == 3 and "a" in pq and "z" not in pq
    assert pq["a"] == 3
    pq["a"] = 0
    assert pq.top() == "a"
    del pq["a"]
    assert sorted(pq) == ["b", "c"]
    assert pq == {"b": 1, "c": 2}
    with pytest.raises(KeyError):
        pq["a"]
    with pytest.raises(KeyError):
        del pq["a"]


def test_priority_order_and_reverse():
    rng = random.Random(1)
    data = {k: rng.random() for k in range(100)}
    assert list(PQDict(data).popkeys()) == sorted(data, key=data.get)
    assert list(maxpq(data).popkeys()) == sorted(data, key=data.get, reverse=True)


def test_key_function_keeps_original_values():
    pq = PQDict({"x": (3, "p"), "y": (1, "q")}, key=lambda k, v: v[0])
    assert pq.keyfn is not None
    assert pq.popitem() == ("y", (1, "q"))
    assert pq.topvalue() == (3, "p")


def test_pop_variants():
    pq = minpq(a=2, b=1, c=3)
    assert pq.pop() == "b"
    assert pq.pop("c") == 3
    assert pq.pop("zz", None) is None
    assert pq.popvalue() == 2
    assert pq.pop(default="empty") == "empty"
    assert pq.popvalue(default=0) == 0
    for fn in (pq.pop, pq.popitem, pq.top, pq.topitem, pq.popvalue):
        with pytest.raises(KeyError):
            fn()
    with pytest.raises(KeyError):
        pq.pop("missing")
    assert pq.top("d") == "d" and pq.topvalue("d") == "d"


def test_add_update_items():
    pq = PQDict()
    pq.additem("a", 1)
    with pytest.raises(KeyError):
        pq.additem("a", 2)
    pq.updateitem("a", 5)
    with pytest.raises(KeyError):
        pq.updateitem("b", 1)
    assert pq.topitem() == ("a", 5)


def test_pushpopitem():
    pq = minpq(a=5, b=7)
    assert pq.pushpopitem("c", 1) == ("c", 1)
    assert "c" not in pq
    assert pq.pushpopitem("d", 6) == ("a", 5)
    assert pq == {"b": 7, "d": 6}
    with pytest.raises(KeyError):
        pq.pushpopitem("b", 0)
    assert PQDict().pushpopitem("x", 1) == ("x", 1)


def test_replace_key_and_swap_priority():
    pq = minpq(a=1, b=2)
    pq.replace_key("a", "z")
    assert pq == {"z": 1, "b": 2}
    with pytest.raises(KeyError):
        pq.replace_key("z", "b")
    pq.swap_priority("z", "b")
    assert pq == {"z": 2, "b": 1}
    assert pq.top() == "b"


def test_heapify_after_in_place_mutation():
    pq = PQDict({"a": [3], "b": [2], "c": [1]}, key=lambda k, v: v[0])
    pq["c"][0] = 10
    pq.heapify("c")
    assert pq.top() == "b"
    pq["a"][0] = -1
    pq["b"][0] = 20
    pq.heapify()
    assert list(pq.popkeys()) == ["a", "c", "b"]


def test_invalid_priorities_leave_state_intact():
    pq = minpq(a=1, b=2)
    with pytest.raises(ValueError):
        pq["a"] = math.nan
    with pytest.raises(ValueError):
        pq.update({"a": 0, "c": math.nan})
    with pytest.raises(ValueError):
        pq.update({"a": math.nan, "c": 0})
    with pytest.raises(TypeError):
        pq["c"] = "not a number"
    assert pq == {"a": 1, "b": 2}
    assert list(pq.popkeys()) == ["a", "b"]


def test_bulk_update_mixed():
    pq = minpq({i: i for i in range(100)})
    pq.update({i: -i for i in range(50, 150)})
    assert len(pq) == 150
    assert list(pq.popvalues()) == sorted([*range(50), *(-i for i in range(50, 150))])


def test_copy_pickle_repr():
    pq = PQDict({"a": 2, "b": 1}, reverse=True)
    clone = pq.copy()
    clone.pop()
    assert len(pq) == 2
    assert copy.copy(pq) == pq
    restored = pickle.loads(pickle.dumps(pq))
    assert restored == pq and restored.reverse and restored.top() == "a"
    assert repr(minpq(a=1)) == "PQDict({'a': 1})"


def test_fromkeys_and_clear():
    pq = PQDict.fromkeys("abc", 0)
    assert pq == {"a": 0, "b": 0, "c": 0}
    pq.clear()
    assert not pq
    pq["x"] = 1
    assert pq.popitem() == ("x", 1)


def test_payload_references_are_released():
    import gc
    import weakref

    class Payload:
        pass

    obj = Payload()
    ref = weakref.ref(obj)
    pq = PQDict({"k": obj}, key=lambda k, v: 0)
    pq.pop()
    del obj
    gc.collect()  # PyPy has no reference counting
    assert ref() is None


def test_nsmallest_nlargest():
    data = {k: (k * 37) % 101 for k in range(50)}
    assert nsmallest(5, data) == sorted(data, key=data.get)[:5]
    assert nlargest(5, data) == sorted(data, key=data.get, reverse=True)[:5]
    assert nsmallest(2, data, key=lambda k, v: -v) == nlargest(2, data)
    assert nsmallest(100, {}) == []


def test_matches_pqdict_reference():
    pqdict = pytest.importorskip("pqdict")
    rng = random.Random(42)
    ours, ref = PQDict(), pqdict.pqdict()
    for _ in range(5000):
        op = rng.random()
        k = rng.randrange(200)
        if op < 0.5:
            ours[k] = ref[k] = rng.random()  # unique priorities: no tie ambiguity
        elif op < 0.7 and k in ref:
            assert ours.pop(k) == ref.pop(k)
        elif op < 0.9 and ref:
            assert ours.popitem() == ref.popitem()
        assert len(ours) == len(ref)
        if ref:
            assert ours.topitem() == ref.topitem()
    assert dict(ours) == dict(ref)


def test_dict_operations():
    pq = minpq(a=3, b=1)
    assert pq.get("a") == 3 and pq.get("z") is None and pq.get("z", 0) == 0
    assert pq.setdefault("a", 9) == 3 and pq.setdefault("c", 2) == 2 and pq["c"] == 2
    assert list(reversed(pq)) == ["c", "b", "a"]
    merged = pq | {"a": 0, "d": 5}
    assert isinstance(merged, PQDict) and merged.top() == "a" and pq["a"] == 3
    rmerged = {"a": 7, "z": -1} | pq
    assert isinstance(rmerged, PQDict) and rmerged == {"a": 3, "z": -1, "b": 1, "c": 2}
    assert list(rmerged) == ["a", "z", "b", "c"]
    same = pq
    pq |= [("e", -5)]
    assert pq is same and pq.top() == "e"
    with pytest.raises(TypeError):
        pq | [("x", 1)]
    assert dict(pq) == {"a": 3, "b": 1, "c": 2, "e": -5}
    assert pq == dict(pq) and dict(pq) == pq and pq != {"a": 3}
    assert set(pq.keys()) & {"a", "q"} == {"a"}
    assert ("b", 1) in pq.items() and 2 in pq.values()
    assert PQDict.fromkeys("xy", key=lambda k, v: 0)["x"] is None
    with pytest.raises(TypeError):
        hash(pq)


def test_random_parity_with_dict():
    """Every dict operation, including iteration order, must match ``dict``."""
    rng = random.Random(7)
    d, pq = {}, PQDict()
    for _ in range(3000):
        k, v = rng.randrange(30), rng.randrange(-50, 50)
        op = rng.randrange(9)
        if op <= 2:
            d[k] = v
            pq[k] = v
        elif op == 3:
            assert pq.pop(k, None) == d.pop(k, None)
        elif op == 4:
            assert pq.setdefault(k, v) == d.setdefault(k, v)
        elif op == 5:
            assert pq.get(k) == d.get(k)
        elif op == 6:
            extra = {rng.randrange(30): rng.randrange(50) for _ in range(3)}
            d.update(extra)
            pq.update(extra)
        elif op == 7:
            extra = {rng.randrange(30): rng.randrange(50) for _ in range(3)}
            d |= extra
            pq |= extra
        elif op == 8 and k in d:
            del d[k]
            del pq[k]
        assert list(pq) == list(d)
        assert list(pq.items()) == list(d.items())
        assert list(reversed(pq)) == list(reversed(d))
        assert len(pq) == len(d) and pq == d
        if d:
            assert pq.topvalue() == min(d.values())


def test_priority_from_key_and_value():
    # priority = item key's length + value
    fn = lambda k, v: len(k) + v  # noqa: E731
    pq = PQDict({"apple": 0, "fig": 1, "kiwi": 5}, key=fn)
    assert list(PQDict(pq, key=fn).popkeys()) == ["fig", "apple", "kiwi"]
    pq["be"] = 0
    assert pq.top() == "be"
    assert pq.pushpopitem("a", 0) == ("a", 0)  # 1 + 0 beats len("be") = 2
    pq.update({"zz": -5, "fig": 100})
    assert list(pq.popkeys()) == ["zz", "be", "apple", "kiwi", "fig"]


def test_replace_key_and_swap_recompute_key_dependent_priorities():
    fn = lambda k, v: len(k) * v  # noqa: E731 - priority = len(key) * value
    pq = PQDict({"aaa": 1, "b": 2}, key=fn)  # priorities: aaa 3, b 2
    assert pq.top() == "b"
    pq.replace_key("aaa", "a")  # a: 1 * 1 = 1
    assert pq.top() == "a" and pq == {"a": 1, "b": 2}
    pq["cccc"] = 3  # 12
    pq.swap_priority("a", "cccc")  # a: 1 * 3 = 3, cccc: 4 * 1 = 4
    assert pq == {"a": 3, "b": 2, "cccc": 1}
    assert list(pq.popitems()) == [("b", 2), ("a", 3), ("cccc", 1)]


def test_key_dependent_operations_are_atomic():
    # NaN exactly for key "bad" holding value 2.
    fn = lambda k, v: math.nan if (k, v) == ("bad", 2) else v  # noqa: E731
    pq = PQDict({"a": 2, "b": 3}, key=fn)
    with pytest.raises(ValueError):
        pq.replace_key("a", "bad")
    with pytest.raises(ValueError):
        pq["bad"] = 2
    assert pq == {"a": 2, "b": 3} and pq.top() == "a"
    pq2 = PQDict({"bad": 1, "x": 2}, key=fn)
    with pytest.raises(ValueError):
        pq2.swap_priority("bad", "x")  # "bad" would take value 2
    assert pq2 == {"bad": 1, "x": 2} and list(pq2.popkeys()) == ["bad", "x"]


def test_nsmallest_nlargest_see_keys():
    data = {"a": 5, "bb": 5, "ccc": 5}
    assert nsmallest(1, data, key=lambda k, v: len(k)) == ["a"]
    assert nlargest(1, data, key=lambda k, v: len(k)) == ["ccc"]
