"""Standard indexed-priority-queue benchmark.

Phases (each timed on fresh state, best of ``--repeat`` runs):

* insert -- ``n`` individual inserts into an empty queue
* build  -- construct a queue from ``n`` items in one call (heapify)
* update -- ``n`` priority changes of random existing keys
* delete -- remove ``n // 2`` random keys
* pop    -- pop every item in priority order

Implementations:

* ``iheap.PQDict``       -- this library, mapping API (keys/values in Python)
* ``iheap.IndexedHeap``  -- this library, raw handle API (caller keeps keys)
* ``pqdict.pqdict``      -- pure-Python reference (``pip install pqdict``)
* ``heapq (lazy)``       -- stdlib ``heapq`` with the lazy-invalidation recipe
                            from the Python docs; updates/deletes leave stale
                            entries that are skipped on pop

Run: ``python benchmarks/bench.py --n 100000 --markdown``
"""

from __future__ import annotations

import argparse
import gc
import heapq
import itertools
import platform
import random
import sys
import time
from collections.abc import Callable

import iheap

PHASES = ("insert", "build", "update", "delete", "pop")


class Workload:
    def __init__(self, n: int, seed: int = 0) -> None:
        rng = random.Random(seed)
        self.n = n
        self.keys = [f"k{i}" for i in range(n)]
        self.priorities = [rng.random() for _ in range(n)]
        self.items = dict(zip(self.keys, self.priorities))
        self.updates = [(rng.choice(self.keys), rng.random()) for _ in range(n)]
        self.deletes = rng.sample(self.keys, n // 2)


# -- adapters: each returns {phase: (setup, run)} -----------------------------
# ``setup()`` builds the state a phase starts from (not timed); ``run(state)``
# is the timed body.


def pqdict_like(factory: Callable[..., object]) -> dict:
    def insert(w: Workload) -> None:
        pq = factory()
        for k, p in zip(w.keys, w.priorities):
            pq[k] = p

    def update(w: Workload, pq) -> None:
        for k, p in w.updates:
            pq[k] = p

    def delete(w: Workload, pq) -> None:
        for k in w.deletes:
            del pq[k]

    def pop(w: Workload, pq) -> None:
        popitem = pq.popitem
        for _ in range(len(pq)):
            popitem()

    return {
        "insert": (lambda w: None, lambda w, _: insert(w)),
        "build": (lambda w: None, lambda w, _: factory(w.items)),
        "update": (lambda w: factory(w.items), update),
        "delete": (lambda w: factory(w.items), delete),
        "pop": (lambda w: factory(w.items), pop),
    }


def raw_heap() -> dict:
    """Raw handle API; the caller keeps a key -> handle dict and handle -> key list."""

    def build(w: Workload):
        heap = iheap.IndexedHeap()
        handles = heap.push_many(w.priorities)
        return heap, dict(zip(w.keys, handles)), list(w.keys)

    def insert(w: Workload, _) -> None:
        heap, index, keys = iheap.IndexedHeap(), {}, []
        push = heap.push
        for k, p in zip(w.keys, w.priorities):
            index[k] = push(p)
            keys.append(k)

    def update(w: Workload, state) -> None:
        heap, index, _ = state
        upd = heap.update
        for k, p in w.updates:
            upd(index[k], p)

    def delete(w: Workload, state) -> None:
        heap, index, keys = state
        remove = heap.remove
        for k in w.deletes:
            h = index.pop(k)
            remove(h)
            keys[h] = None

    def pop(w: Workload, state) -> None:
        heap, index, keys = state
        hpop = heap.pop
        for _ in range(len(heap)):
            h, _ = hpop()
            del index[keys[h]]

    return {
        "insert": (lambda w: None, insert),
        "build": (lambda w: None, lambda w, _: build(w)),
        "update": (build, update),
        "delete": (build, delete),
        "pop": (build, pop),
    }


class LazyHeapq:
    """``heapq`` priority queue with lazy invalidation (Python docs recipe)."""

    __slots__ = ("heap", "entries", "counter")
    REMOVED = object()

    def __init__(self, items: dict | None = None) -> None:
        self.counter = itertools.count()
        self.entries: dict = {}
        self.heap: list = []
        for k, p in (items or {}).items():
            entry = [p, next(self.counter), k]
            self.entries[k] = entry
            self.heap.append(entry)
        heapq.heapify(self.heap)

    def __len__(self) -> int:
        return len(self.entries)

    def __setitem__(self, key, priority) -> None:
        old = self.entries.pop(key, None)
        if old is not None:
            old[-1] = self.REMOVED
        entry = [priority, next(self.counter), key]
        self.entries[key] = entry
        heapq.heappush(self.heap, entry)

    def __delitem__(self, key) -> None:
        self.entries.pop(key)[-1] = self.REMOVED

    def popitem(self):
        heap, removed = self.heap, self.REMOVED
        while True:
            priority, _, key = heapq.heappop(heap)
            if key is not removed:
                del self.entries[key]
                return key, priority


def implementations() -> dict[str, dict]:
    impls = {
        "iheap.PQDict": pqdict_like(iheap.PQDict),
        "iheap.IndexedHeap": raw_heap(),
    }
    try:
        import pqdict

        impls["pqdict"] = pqdict_like(pqdict.pqdict)
    except ImportError:
        print("pqdict not installed; skipping", file=sys.stderr)
    impls["heapq (lazy)"] = pqdict_like(LazyHeapq)
    return impls


def measure(phase, w: Workload, repeat: int) -> float:
    setup, run = phase
    best = float("inf")
    for _ in range(repeat):
        state = setup(w)
        gc.collect()
        gc.disable()
        try:
            t0 = time.perf_counter()
            run(w, state)
            best = min(best, time.perf_counter() - t0)
        finally:
            gc.enable()
    return best


def ops(w: Workload, phase: str) -> int:
    return w.n // 2 if phase == "delete" else w.n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, nargs="+", default=[100_000])
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--markdown", action="store_true", help="emit a Markdown table")
    args = parser.parse_args()

    impls = implementations()
    print(f"Python {platform.python_version()} on {platform.machine()} ({platform.system()}), "
          f"iheap {iheap.__version__}, best of {args.repeat}\n")
    for n in args.n:
        w = Workload(n)
        results = {name: {p: measure(impl[p], w, args.repeat) for p in PHASES} for name, impl in impls.items()}
        baseline = results.get("pqdict")
        print(f"n = {n:,} -- ns per operation (lower is better)" + (", speed-up vs pqdict" if baseline else ""))
        if args.markdown:
            print()
            print("| implementation | " + " | ".join(PHASES) + " |")
            print("|---|" + "---:|" * len(PHASES))
        for name, res in results.items():
            cells = []
            for p in PHASES:
                ns = res[p] / ops(w, p) * 1e9
                cell = f"{ns:,.0f}"
                if baseline and name != "pqdict":
                    cell += f" ({baseline[p] / res[p]:.1f}x)"
                cells.append(cell)
            if args.markdown:
                print(f"| {name} | " + " | ".join(cells) + " |")
            else:
                print(f"  {name:<18}" + "".join(f"{p}={c:<18}" for p, c in zip(PHASES, cells)))
        print()


if __name__ == "__main__":
    main()
