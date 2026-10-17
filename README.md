# iheap

A fast **indexed priority queue** for Python with a Rust core. It's a drop-in
style replacement for [`pqdict`](https://github.com/nvictus/pqdict), and is
**3–20× faster**.

```python
from iheap import PQDict

pq = PQDict({"a": 3, "b": 1, "c": 2})   # key -> priority, min on top
pq["d"] = 0                             # insert
pq["a"] = -1                            # update priority in O(log n)
del pq["c"]                             # delete any key in O(log n)
pq.top()                                # 'a'
list(pq.popitems())                     # [('a', -1), ('d', 0), ('b', 1)]
```

```
pip install iheap
```

Prebuilt `abi3` wheels cover CPython ≥ 3.10 on Linux (glibc and musl, x86_64
and aarch64), macOS (x86_64 and arm64) and Windows (x64).

## Design

Only priorities (as `f64`) and integer handles cross into Rust. Keys and
values (the payload) never leave Python.

```
           Python (iheap.PQDict)                 Rust (iheap-core)
  ┌───────────────────────────────────┐   ┌────────────────────────────────┐
  │ _handles: dict[key -> handle]     │   │ nodes:     [(priority, handle)]│ 4-ary heap
  │ _keys:    list[handle -> key]     │──▶│ positions: [handle -> node idx]│
  │ _values:  list[handle -> value]   │   │ free:      [recycled handles]  │
  └───────────────────────────────────┘   └────────────────────────────────┘
                    only f64 priorities and u32 handles cross ▲
```

* **Handles, not keys.** The Rust heap gives out dense `u32` handles and
  recycles them, so Python keeps its key/value data in plain lists indexed by
  handle. Rust never holds a Python object and never calls back into Python.
* **`f64` priorities.** Comparing native floats is what makes the core fast.
  Ints are exact up to 2⁵³. NaN is rejected with `ValueError`.
* **4-ary heap with hole-based sifting.** It is half as deep as a binary heap,
  and a node's four children (4 × 16 B) fit in one 64-byte cache line. Sifting
  moves nodes into a "hole" instead of swapping them. Measured against other
  arities: about 1.5× faster than binary; 8-ary was within noise.
* **Max-heap by negation.** Priorities are negated on the way in and out, so
  the comparison loop has no extra branch.
* **Batch operations.** `push_many` and `update_many` switch to an `O(n)`
  heapify when the batch is large enough to make that cheaper. Building a
  `PQDict` takes one native call.
* **Clean layering.** `crates/iheap-core` is pure Rust with no PyO3 dependency
  and its own tests. `crates/iheap-py` is a thin PyO3 binding.
  `python/iheap/_pqdict.py` implements the mapping API.

## API

### `PQDict(data=None, *, key=None, reverse=False)`

A `MutableMapping` whose items are ordered by priority. The priority is
`key(value)` if a `key` function is given, otherwise the value itself.
`reverse=True` makes it a max-queue. Ties are popped in unspecified order.

`PQDict` supports **every `dict` operation**: `pq[k]`, `pq[k] = v`,
`del pq[k]`, `k in pq`, `len`, `iter`, `reversed`, `get`, `setdefault`,
`pop(k[, default])`, `update`, `|`, `|=`, `keys`/`values`/`items` views,
`clear`, `copy`, `fromkeys`, equality with any mapping, and pickling. As with
`dict`, iteration follows **insertion order**. The one intended difference is
`popitem()`, which removes the **top-priority** item instead of the last one
inserted.

| Method | Description |
|---|---|
| `pq[k] = v`, `del pq[k]`, `pq[k]`, `k in pq`, `len(pq)` | mapping protocol; inserts, updates and deletes are `O(log n)` |
| `top(default)`, `topvalue(default)`, `topitem()` | look at the top item |
| `pop()`, `popvalue(default)`, `popitem()` | remove the top item (`pop(key[, default])` keeps dict semantics) |
| `additem(k, v)` / `updateitem(k, v)` | strict insert / strict update |
| `pushpopitem(k, v)` | push then pop, done as a single sift |
| `replace_key(k, new_k)`, `swap_priority(k1, k2)` | change keys in place (no heap work) |
| `popkeys()`, `popvalues()`, `popitems()` | destructive iteration in priority order |
| `heapify(key=None)` | re-prioritize after a value was mutated in place |
| `update(mapping)` | bulk insert/update with one native call (atomic) |
| `copy()`, pickling, `fromkeys()` | |

Module functions: `minpq(**kw)`, `maxpq(**kw)`, `nsmallest(n, mapping, key=None)`,
`nlargest(n, mapping, key=None)`.

Differences from `pqdict`: priorities must be real numbers, and the custom
comparator `precedes=` is replaced by `reverse=`.

### `IndexedHeap(*, reverse=False, capacity=0)`

The raw native heap, for when you want to manage your own key ↔ handle mapping:

```python
from iheap import IndexedHeap

heap = IndexedHeap()
h = heap.push(3.5)               # -> handle (int)
heap.update(h, 1.0)              # -> old priority
heap.remove(h)                   # -> priority
handles = heap.push_many([5, 2, 8])
heap.peek()                      # (handle, priority) or None
heap.pop()                       # (handle, priority); IndexError if empty
heap.pop_many(2)
heap.push_pop(4.0)               # fused push+pop; reuses the popped handle
```

Handles stay valid until their entry is popped or removed, are always below
`heap.handle_bound`, and are reused afterwards.

## Benchmark

`benchmarks/bench.py` runs a standard set of indexed-priority-queue
operations. Each phase starts from fresh state, uses random `float`
priorities and `str` keys, and reports the best of 5 runs:

* **insert**: `n` single inserts into an empty queue
* **build**: construct from `n` items in one call
* **update**: `n` priority changes on random existing keys
* **delete**: `n/2` deletions of random keys
* **pop**: pop everything in priority order

Implementations compared:

* `iheap.PQDict`: this library's mapping API
* `iheap.IndexedHeap`: the raw handle API, with the caller keeping a key → handle `dict`
* [`pqdict`](https://pypi.org/project/pqdict/) 1.4: the pure-Python reference
* `heapq (lazy)`: the stdlib "mark removed, skip on pop" recipe from the
  Python docs. Its update and delete are cheap because they defer the work
  to pop.

Results on Apple M4 Max, CPython 3.13.7, iheap 0.1.0. Each cell is **ns per
operation, lower is better**, with the speed-up over `pqdict` in parentheses.

**n = 10,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 154 (3.4x) | 48 (12.2x) | 137 (6.7x) | 139 (5.5x) | 183 (12.8x) |
| iheap.IndexedHeap | 60 (8.9x) | 42 (13.9x) | 79 (11.6x) | 81 (9.4x) | 117 (20.0x) |
| pqdict | 533 | 587 | 915 | 757 | 2,329 |
| heapq (lazy) | 191 (2.8x) | 111 (5.3x) | 201 (4.6x) | 83 (9.2x) | 390 (6.0x) |

**n = 100,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 176 (3.6x) | 60 (11.3x) | 288 (5.6x) | 285 (5.0x) | 313 (11.8x) |
| iheap.IndexedHeap | 76 (8.2x) | 52 (12.9x) | 123 (13.1x) | 201 (7.2x) | 237 (15.6x) |
| pqdict | 626 | 675 | 1,612 | 1,436 | 3,691 |
| heapq (lazy) | 238 (2.6x) | 154 (4.4x) | 447 (3.6x) | 244 (5.9x) | 960 (3.8x) |

**n = 1,000,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 246 (2.9x) | 109 (8.1x) | 846 (3.1x) | 815 (3.0x) | 873 (9.5x) |
| iheap.IndexedHeap | 156 (4.6x) | 92 (9.7x) | 623 (4.2x) | 584 (4.2x) | 694 (11.9x) |
| pqdict | 724 | 887 | 2,591 | 2,429 | 8,283 |
| heapq (lazy) | 331 (2.2x) | 232 (3.8x) | 803 (3.2x) | 570 (4.3x) | 2,336 (3.5x) |

The Rust core on its own (`cargo run --release --example bench -p iheap-core`)
takes 5–20 ns per insert/update/delete and 40–70 ns per pop at n = 100k–1M.
So most of the time measured from Python is the interpreter itself: method
dispatch, dict lookups on string keys, and cache misses on Python objects.
The largest remaining cost is the ~100 ns `PQDict` adds per call on top of
the raw API, which is the price of a Python-level `__setitem__` frame.

Optimizations that were tried and kept:

* inlining helper calls on hot paths: −15–25% per op
* bulk build through `push_many` and C-level `zip`/`dict`: build went from 3× to 11–12× faster than `pqdict`
* 4-ary instead of binary heap: about 1.5× faster core

Tried and rejected because they made no difference or were slower:

* Floyd's bottom-up pop
* a branch-free 4-way tournament for choosing the minimum child (about 25% slower)

Reproduce with:

```
uv venv && uv pip install maturin pqdict
maturin develop --release
python benchmarks/bench.py --n 10000 100000 1000000 --markdown
```

## Development

```
cargo test -p iheap-core                  # Rust core tests (incl. randomized model test)
maturin develop --release && pytest       # Python tests (incl. differential test vs pqdict)
```

Releasing: bump `version` in the root `Cargo.toml`, then push a `vX.Y.Z` tag.
CI builds the wheels and sdist and publishes them to PyPI through Trusted
Publishing.

## License

MIT
