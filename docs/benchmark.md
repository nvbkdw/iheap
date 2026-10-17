# Benchmark

`benchmarks/bench.py` measures the standard indexed-priority-queue operations
against the common alternatives.

## Methodology

Each phase starts from fresh state and uses random `float` priorities and
`str` keys. Each result is the best of 5 runs, with the garbage collector
disabled while timing.

| Phase | What is timed |
|---|---|
| **insert** | `n` single inserts into an empty queue |
| **build** | constructing a queue from `n` items in one call |
| **update** | `n` priority changes on random existing keys |
| **delete** | `n/2` deletions of random keys |
| **pop** | popping every item in priority order |

| Implementation | Notes |
|---|---|
| `iheap.PQDict` | this library's mapping API |
| `iheap.IndexedHeap` | the raw handle API; the caller keeps a key → handle `dict` |
| [`pqdict`](https://pypi.org/project/pqdict/) 1.4 | the pure-Python reference |
| `heapq (lazy)` | the stdlib ["mark removed, skip on pop"](https://docs.python.org/3/library/heapq.html#priority-queue-implementation-notes) recipe. Its update and delete are cheap because they defer the work to pop, and stale entries use memory until then. |

## Results

Apple M4 Max, CPython 3.13.7, iheap 0.1.0. Each cell is **ns per operation,
lower is better**, with the speed-up over `pqdict` in parentheses.

=== "n = 10,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 154 (3.4x) | 48 (12.2x) | 137 (6.7x) | 139 (5.5x) | 183 (12.8x) |
    | iheap.IndexedHeap | 60 (8.9x) | 42 (13.9x) | 79 (11.6x) | 81 (9.4x) | 117 (20.0x) |
    | pqdict | 533 | 587 | 915 | 757 | 2,329 |
    | heapq (lazy) | 191 (2.8x) | 111 (5.3x) | 201 (4.6x) | 83 (9.2x) | 390 (6.0x) |

=== "n = 100,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 176 (3.6x) | 60 (11.3x) | 288 (5.6x) | 285 (5.0x) | 313 (11.8x) |
    | iheap.IndexedHeap | 76 (8.2x) | 52 (12.9x) | 123 (13.1x) | 201 (7.2x) | 237 (15.6x) |
    | pqdict | 626 | 675 | 1,612 | 1,436 | 3,691 |
    | heapq (lazy) | 238 (2.6x) | 154 (4.4x) | 447 (3.6x) | 244 (5.9x) | 960 (3.8x) |

=== "n = 1,000,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 246 (2.9x) | 109 (8.1x) | 846 (3.1x) | 815 (3.0x) | 873 (9.5x) |
    | iheap.IndexedHeap | 156 (4.6x) | 92 (9.7x) | 623 (4.2x) | 584 (4.2x) | 694 (11.9x) |
    | pqdict | 724 | 887 | 2,591 | 2,429 | 8,283 |
    | heapq (lazy) | 331 (2.2x) | 232 (3.8x) | 803 (3.2x) | 570 (4.3x) | 2,336 (3.5x) |

### Rust core alone

`cargo run --release --example bench -p iheap-core` measures the heap without
Python involved:

| n | insert | build | update | delete | pop |
|---:|---:|---:|---:|---:|---:|
| 100,000 | 13 ns | 10 ns | 13 ns | 8 ns | 40 ns |
| 1,000,000 | 8 ns | 5 ns | 15 ns | 19 ns | 70 ns |

## Where the time goes

Compared with the end-to-end numbers above, the native heap accounts for only
a small part of each operation. The rest is the interpreter itself: a
cross-language call costs about 18 ns, and the remaining time goes to Python
method dispatch, `dict` lookups on string keys and cache misses on Python
objects. `PQDict` adds about 100 ns per call on top of the raw API, which is
the cost of a Python-level `__setitem__` / `popitem` frame. The only way to
remove that would be to move key storage into Rust, which would contradict
the design goal of keeping the payload in Python.

## Optimization log

| Change | Effect | Outcome |
|---|---|---|
| Inline helper calls on `PQDict` hot paths | −15–25% per op | kept |
| Bulk `PQDict(data)` via `push_many` + C-level `zip`/`dict` | build: 3× → 11–12× vs `pqdict` | kept |
| 4-ary instead of binary heap | ~1.5× faster core; 8-ary within noise | kept (4 children = one 64-byte cache line) |
| Floyd's bottom-up `pop` | no measurable change | rejected |
| Branch-free 4-way tournament for choosing the minimum child | ~25% slower `pop` | rejected |

## Reproducing

```bash
uv venv && uv pip install maturin pqdict
maturin develop --release
python benchmarks/bench.py --n 10000 100000 1000000 --markdown
cargo run --release --example bench -p iheap-core
```
