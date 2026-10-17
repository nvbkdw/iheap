"""Automatic core selection and user configuration."""

import os
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction

import pytest

import iheap
from iheap import PQDict, nsmallest
from iheap._core import NativeIndexedHeap

HAS_NATIVE = NativeIndexedHeap is not None


def test_auto_stays_numeric_for_exact_floats():
    pq = PQDict({"a": 1, "b": 2.5, "c": True, "d": 2**53, "e": Fraction(1, 2)})
    assert pq.priority_type == "auto"
    assert pq.core == ("native" if iheap.NATIVE else "python")


@pytest.mark.parametrize(
    "priority",
    ["text", (1, 2), 2**53 + 1, Decimal("0.1"), Fraction(1, 3)],
)
def test_auto_switches_to_object_core_once(priority):
    pq = PQDict({"a": priority})
    assert pq.core == "object"
    assert pq.top() == "a"


def test_auto_switch_keeps_items_order_and_exactness():
    big = 2**60  # floats are 256 apart here
    pq = PQDict({"x": big + 256, "y": big})  # exact floats: numeric core
    numeric_core = pq.core
    pq["z"] = big + 1  # not exact: switch
    assert numeric_core != "object" and pq.core == "object"
    assert list(pq) == ["x", "y", "z"]
    assert list(pq.popkeys()) == ["y", "z", "x"]  # exact integer ordering


def test_switch_is_sticky_and_survives_copy_and_pickle():
    import pickle

    pq = PQDict({"a": "x"})
    del pq["a"]
    pq["b"] = 1  # numeric again, but the switch is one-way
    assert pq.core == "object"
    assert pq.copy().core == "object"
    assert pickle.loads(pickle.dumps(PQDict({"a": "x"}))).core == "object"


def test_pinned_priority_types():
    with pytest.raises(TypeError):
        PQDict({"a": "text"}, priority_type="float")
    assert PQDict({"a": 1}, priority_type="object").core == "object"
    with pytest.raises(ValueError):
        PQDict(priority_type="decimal")  # type: ignore[arg-type]


def test_backend_argument():
    assert PQDict({"a": 1}, backend="python").core == "python"
    assert PQDict({"a": 1}, backend="auto").core == ("native" if HAS_NATIVE else "python")
    if HAS_NATIVE:
        assert PQDict({"a": 1}, backend="native").core == "native"
    else:
        with pytest.raises(ImportError):
            PQDict(backend="native")
    with pytest.raises(ValueError):
        PQDict(backend="gpu")  # type: ignore[arg-type]
    assert nsmallest(1, {"a": 2, "b": 1}, backend="python") == ["b"]
    assert nsmallest(1, {"a": "y", "b": "x"}) == ["b"]  # auto: object core


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"IHEAP_BACKEND": "python"}, "python"),
        ({"IHEAP_PURE_PYTHON": "1"}, "python"),
        ({"IHEAP_BACKEND": "auto"}, "native" if HAS_NATIVE else "python"),
    ],
)
def test_environment_sets_the_default_backend(env, expected):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("IHEAP_")}
    out = subprocess.run(
        [sys.executable, "-c", "import iheap; print(iheap.DEFAULT_BACKEND, iheap.PQDict({1: 1}).core)"],
        env={**clean, **env},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert out[1] == expected


def test_invalid_environment_backend_fails_loudly():
    env = {k: v for k, v in os.environ.items() if not k.startswith("IHEAP_")}
    result = subprocess.run(
        [sys.executable, "-c", "import iheap"],
        env={**env, "IHEAP_BACKEND": "gpu"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "IHEAP_BACKEND" in result.stderr
