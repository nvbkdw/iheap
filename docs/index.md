# iheap

**iheap** is a fast **indexed priority queue** for Python with a Rust core. It
works as a drop-in style replacement for
[`pqdict`](https://github.com/nvictus/pqdict) and is **3–20× faster**.

An *indexed* priority queue does what `heapq` can't: you can change the
priority of any item, or delete any item, in `O(log n)`. That makes it the
right data structure for Dijkstra/A\*, schedulers, LRU/LFU-style caches,
event simulation and top-k tracking.

```python
from iheap import PQDict

pq = PQDict({"a": 3, "b": 1, "c": 2})   # key -> priority, min on top
pq["d"] = 0                             # insert
pq["a"] = -1                            # update priority in O(log n)
del pq["c"]                             # delete any key in O(log n)
assert pq.top() == "a"
assert list(pq.popitems()) == [("a", -1), ("d", 0), ("b", 1)]
```

## Highlights

- **A full `dict`.** `PQDict` supports every `dict` operation (`get`,
  `setdefault`, `|`, `|=`, views, insertion-order iteration, pickling) plus
  the whole `pqdict` priority-queue API.
- **The payload stays in Python.** Only `float` priorities and integer handles
  cross into Rust. Your keys and values are never copied or converted.
- **Native speed where it counts.** The core is a cache-friendly 4-ary heap
  that takes 5–70 ns per operation. Bulk construction and bulk updates run as
  a single `O(n)` native call.
- **Two levels of API.** Use [`PQDict`][iheap.PQDict] for convenience, or the
  raw handle-based [`IndexedHeap`][iheap.IndexedHeap] when you manage your
  own storage.
- **Easy to install.** Prebuilt `abi3` wheels for CPython ≥ 3.10 on Linux,
  macOS and Windows. Typed (`py.typed`).

## Install

```bash
pip install iheap
```

Next, read the [Getting started](getting-started.md) guide, browse the
[API reference](api.md), or see the [benchmark](benchmark.md).
