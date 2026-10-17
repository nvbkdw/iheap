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

📖 **Documentation:** <https://nvbkdw.github.io/iheap/>. It covers a
getting-started guide, the API reference, the benchmark and development notes.

pip picks the best implementation for your interpreter automatically:

| Interpreter | Core |
|---|---|
| CPython ≥ 3.10 with the GIL | native Rust (`abi3` wheel), lock-free: the GIL is the lock |
| CPython 3.14t, free-threaded | native Rust (`cp314t` wheel), mutex per heap |
| CPython 3.13t, PyPy, other platforms | pure Python (`py3-none-any` wheel), same API |

Native wheels cover Linux (glibc and musl, x86_64 and aarch64), macOS
(x86_64 and arm64) and Windows (x64). `iheap.NATIVE` tells you which core
you got. `IndexedHeap` is safe to share between threads; guard a `PQDict`
that several threads mutate with a lock.

## Design

Only priorities (as `f64`) and integer handles cross into Rust. Keys and
values (the payload) never leave Python.

```
           Python (iheap.PQDict)                     Rust (iheap-core)
  ┌───────────────────────────────────┐   ┌─────────────────────────────────────┐
  │ _handles: dict[key -> handle]     │   │ 8-ary tournament tree               │
  │ _keys:    list[handle -> key]     │──▶│   leaves[h]  = priority of handle h │
  │ _values:  list[handle -> value]   │   │   node       = winner of 8 children │
  └───────────────────────────────────┘   └─────────────────────────────────────┘
                    only f64 priorities and u32 handles cross ▲
```

* **Handles, not keys.** The Rust core gives out dense `u32` handles and
  recycles them, so Python keeps its key/value data in plain lists indexed by
  handle. Rust never holds a Python object and never calls back into Python.
* **An 8-ary tournament (winner) tree, not a binary heap.** Leaf `h` holds the
  priority of handle `h`, so no position map is needed. Each internal node
  stores the winner of 8 children. A sibling group is one 64- or 128-byte
  cache-line block.
* **The path is known in advance.** Every operation writes one leaf and
  replays the path to the root, and the node above leaf `h` on each level is
  `h >> 3·level`. In a heap's sift-down, each load must wait for the previous
  comparison. Here, the winner among the *other* siblings on each level
  doesn't depend on the value being replayed, so the CPU computes all levels
  in parallel. The serial work per level is a single compare-and-select. On
  aarch64, the 8-way reductions use NEON.
* **Early exit.** Updates stop once a node's winner is unchanged, so insert,
  decrease-key and changes to non-winning entries usually take one or two
  comparisons.
* **Order-preserving `u64` keys.** Priorities are stored as integers with the
  same ordering. This gives integer compares and a total order: `-0.0` sorts
  before `+0.0`, and both round-trip exactly. A vacant-leaf sentinel can never
  collide with a real priority, because it corresponds to NaN, which is
  rejected. A max-queue complements the keys.
* **Growth only appends.** Bulk loads (`push_many`, `PQDict(data)`) rebuild
  the tree in `O(n)`, and building a `PQDict` takes one native call.
* **Clean layering.** `crates/iheap-core` is pure Rust with no PyO3 dependency
  and has its own tests. `crates/iheap-py` is a thin PyO3 binding.
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
| `pushpopitem(k, v)` | push then pop, done as a single tree replay |
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
| iheap.PQDict | 155 (3.4x) | 48 (11.6x) | 122 (7.1x) | 134 (5.6x) | 139 (16.7x) |
| iheap.IndexedHeap | 56 (9.4x) | 38 (14.4x) | 55 (15.6x) | 70 (10.8x) | 87 (26.6x) |
| pqdict | 527 | 552 | 867 | 759 | 2,322 |
| heapq (lazy) | 185 (2.9x) | 114 (4.9x) | 234 (3.7x) | 76 (9.9x) | 425 (5.5x) |

**n = 100,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 178 (3.4x) | 61 (11.1x) | 264 (6.0x) | 317 (4.5x) | 293 (14.9x) |
| iheap.IndexedHeap | 75 (8.0x) | 53 (12.6x) | 118 (13.5x) | 149 (9.5x) | 184 (23.8x) |
| pqdict | 603 | 674 | 1,593 | 1,415 | 4,376 |
| heapq (lazy) | 237 (2.5x) | 147 (4.6x) | 487 (3.3x) | 283 (5.0x) | 970 (4.5x) |

**n = 1,000,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 260 (3.0x) | 110 (8.6x) | 660 (4.1x) | 760 (3.3x) | 754 (11.9x) |
| iheap.IndexedHeap | 151 (5.1x) | 87 (10.9x) | 459 (5.9x) | 491 (5.1x) | 614 (14.6x) |
| pqdict | 769 | 950 | 2,688 | 2,512 | 8,977 |
| heapq (lazy) | 360 (2.1x) | 245 (3.9x) | 860 (3.1x) | 552 (4.6x) | 2,473 (3.6x) |

### The Rust core alone

`cargo run --release --example bench -p iheap-core` measures the core with
Python out of the picture. The table compares the current tournament tree
with the 4-ary binary heap used at first, in ns per operation:

| n | insert | build | update | delete | pop |
|---:|---:|---:|---:|---:|---:|
| 10k | 3.0 → 3.0 (1.0×) | 3.5 → 2.2 (1.6×) | 3.2 → 2.0 (1.6×) | 3.9 → 2.5 (1.6×) | 25.2 → 17.1 (1.5×) |
| 100k | 5.3 → 4.3 (1.2×) | 4.1 → 2.1 (2.0×) | 10.5 → 3.5 (3.0×) | 5.9 → 3.1 (1.9×) | 37.5 → 20.3 (1.8×) |
| 1M | 8.0 → 4.9 (1.6×) | 4.7 → 2.6 (1.8×) | 18.2 → 5.6 (3.2×) | 14.1 → 5.4 (2.6×) | 64.0 → 29.6 (2.2×) |

From n = 100k up, the core is **2.1× faster** (geometric mean over the five
operations). In the Python benchmark above, the core accounts for only 5–20%
of the time; the rest is the interpreter itself (method dispatch, `dict`
lookups on string keys, object allocation). End to end, the new core makes
`IndexedHeap` 1.1–1.4× faster and `PQDict` up to 1.3× faster. The
[benchmark docs](https://nvbkdw.github.io/iheap/benchmark/) include the full
optimization log, with every experiment that was kept or rejected.

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
