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

iheap runs on CPython 3.11+ on every platform. pip picks the best
implementation for your interpreter automatically:

| Interpreter | Core |
|---|---|
| CPython ≥ 3.11 with the GIL | native Rust (`cp311-abi3` wheel), lock-free: the GIL is the lock |
| CPython 3.14t, free-threaded | native Rust (`cp314t` wheel), mutex per heap |
| CPython 3.15t+, free-threaded | native Rust (`cp315-abi3t` wheel), mutex per heap |
| CPython 3.13t, PyPy, GraalPy, Pyodide, … | pure Python (`py3-none-any` wheel), same API |

Native wheels cover Linux glibc (x86_64, i686, aarch64, armv7, ppc64le,
s390x), Linux musl (x86_64, i686, aarch64, armv7), macOS (x86_64, arm64) and
Windows (x64, x86, arm64). `iheap.NATIVE` tells you whether the Rust core is
the default, and `pq.core` which core a queue uses. Override the choice with
`IHEAP_BACKEND=auto|native|python` or `PQDict(..., backend=...)`.
`IndexedHeap` is safe to share between threads; guard a `PQDict` that several
threads mutate with a lock.

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

### `PQDict(data=None, *, key=None, reverse=False, priority_type="auto", backend=None)`

A `MutableMapping` whose items are ordered by priority. The priority is
`key(item_key, value)` if a `key` function is given, otherwise the value itself.
`reverse=True` makes it a max-queue. Ties are popped in unspecified order.

Priorities can be numbers or any objects that support `<` (strings, tuples,
dates, …). With `priority_type="auto"` a queue runs on the Rust core while
every priority is exactly representable as a `float`, and moves once to the
pure-Python object core otherwise, so ordering is always exact. Pin
`priority_type="float"` (numeric only; fastest) or `"object"`. `backend`
chooses the numeric core: `"auto"`, `"native"` or `"python"`.

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
| `replace_key(k, new_k)`, `swap_priority(k1, k2)` | change keys in place (recomputes priorities if a `key` function is set) |
| `popkeys()`, `popvalues()`, `popitems()` | destructive iteration in priority order |
| `heapify(key=None)` | re-prioritize after a value was mutated in place |
| `update(mapping)` | bulk insert/update with one native call (atomic) |
| `copy()`, pickling, `fromkeys()` | |
| `core` | `"native"`, `"python"` or `"object"`: the core in use right now |

Module functions: `minpq(**kw)`, `maxpq(**kw)`,
`nsmallest(n, mapping, key=None, priority_type="auto", backend=None)`,
`nlargest(...)` (same arguments), `heap_class(backend)`.

`ObjectHeap(*, reverse=False)` is the low-level heap for arbitrary comparable
priorities, with the same API as `IndexedHeap`.

Differences from `pqdict`:

- priorities that aren't exact floats (strings, tuples, big integers, …) are
  handled by a pure-Python core, and numbers by the Rust core, automatically;
- the custom comparator `precedes=` is replaced by `reverse=`;
- the `key` function receives the item's key as well as its value,
  `key(item_key, value)`. To port `key=f`, write `key=lambda k, v: f(v)`.

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

Results on Apple M4 Max, CPython 3.13.7, iheap 0.3.0. Each cell is **ns per
operation, lower is better**, with the speed-up over `pqdict` in parentheses.
`PQDict` runs with the default `priority_type="auto"`.

**n = 10,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 239 (2.2x) | 60 (9.0x) | 153 (5.5x) | 134 (5.5x) | 141 (15.6x) |
| iheap.IndexedHeap | 58 (8.9x) | 37 (14.5x) | 59 (14.3x) | 71 (10.4x) | 89 (24.7x) |
| pqdict | 518 | 540 | 843 | 744 | 2,201 |
| heapq (lazy) | 184 (2.8x) | 109 (5.0x) | 199 (4.2x) | 75 (10.0x) | 383 (5.7x) |

**n = 100,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 190 (3.0x) | 58 (10.4x) | 236 (6.3x) | 231 (5.1x) | 226 (16.1x) |
| iheap.IndexedHeap | 70 (8.1x) | 50 (12.1x) | 92 (16.1x) | 125 (9.4x) | 124 (29.4x) |
| pqdict | 570 | 603 | 1,475 | 1,177 | 3,637 |
| heapq (lazy) | 218 (2.6x) | 134 (4.5x) | 411 (3.6x) | 198 (5.9x) | 867 (4.2x) |

**n = 1,000,000**

| implementation | insert | build | update | delete | pop |
|---|---:|---:|---:|---:|---:|
| iheap.PQDict | 285 (2.6x) | 97 (9.3x) | 690 (3.8x) | 761 (3.3x) | 739 (11.7x) |
| iheap.IndexedHeap | 148 (5.0x) | 85 (10.6x) | 423 (6.3x) | 498 (5.0x) | 605 (14.3x) |
| pqdict | 733 | 903 | 2,646 | 2,511 | 8,645 |
| heapq (lazy) | 375 (2.0x) | 265 (3.4x) | 885 (3.0x) | 623 (4.0x) | 2,423 (3.6x) |

### The Rust core alone

`cargo run --release --example bench -p iheap-core` measures the core with
Python out of the picture. The table compares the current tournament tree
with the 4-ary binary heap used at first, in ns per operation:

| n | insert | build | update | delete | pop |
|---:|---:|---:|---:|---:|---:|
| 10k | 3.0 → 2.9 (1.0×) | 3.5 → 2.3 (1.5×) | 3.2 → 1.9 (1.7×) | 3.9 → 2.5 (1.6×) | 25.2 → 17.3 (1.5×) |
| 100k | 5.3 → 3.9 (1.4×) | 4.1 → 2.2 (1.9×) | 10.5 → 2.9 (3.6×) | 5.9 → 3.0 (2.0×) | 37.5 → 20.6 (1.8×) |
| 1M | 8.0 → 4.9 (1.6×) | 4.7 → 2.6 (1.8×) | 18.2 → 5.3 (3.4×) | 14.1 → 5.8 (2.4×) | 64.0 → 29.2 (2.2×) |

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
