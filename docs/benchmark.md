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
    | iheap.PQDict | 155 (3.4x) | 48 (11.6x) | 122 (7.1x) | 134 (5.6x) | 139 (16.7x) |
    | iheap.IndexedHeap | 56 (9.4x) | 38 (14.4x) | 55 (15.6x) | 70 (10.8x) | 87 (26.6x) |
    | pqdict | 527 | 552 | 867 | 759 | 2,322 |
    | heapq (lazy) | 185 (2.9x) | 114 (4.9x) | 234 (3.7x) | 76 (9.9x) | 425 (5.5x) |

=== "n = 100,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 178 (3.4x) | 61 (11.1x) | 264 (6.0x) | 317 (4.5x) | 293 (14.9x) |
    | iheap.IndexedHeap | 75 (8.0x) | 53 (12.6x) | 118 (13.5x) | 149 (9.5x) | 184 (23.8x) |
    | pqdict | 603 | 674 | 1,593 | 1,415 | 4,376 |
    | heapq (lazy) | 237 (2.5x) | 147 (4.6x) | 487 (3.3x) | 283 (5.0x) | 970 (4.5x) |

=== "n = 1,000,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 260 (3.0x) | 110 (8.6x) | 660 (4.1x) | 760 (3.3x) | 754 (11.9x) |
    | iheap.IndexedHeap | 151 (5.1x) | 87 (10.9x) | 459 (5.9x) | 491 (5.1x) | 614 (14.6x) |
    | pqdict | 769 | 950 | 2,688 | 2,512 | 8,977 |
    | heapq (lazy) | 360 (2.1x) | 245 (3.9x) | 860 (3.1x) | 552 (4.6x) | 2,473 (3.6x) |

### Rust core alone

`cargo run --release --example bench -p iheap-core` measures the core with no
Python involved. Each phase repeats until at least 200 ms has been measured,
and the best repetition is reported. The table compares the current 8-ary
tournament tree with the 4-ary binary heap the project started with (ns per
operation, speed-up in parentheses):

| n | insert | build | update | delete | pop |
|---:|---:|---:|---:|---:|---:|
| 10k | 3.0 → 3.0 (1.0×) | 3.5 → 2.2 (1.6×) | 3.2 → 2.0 (1.6×) | 3.9 → 2.5 (1.6×) | 25.2 → 17.1 (1.5×) |
| 100k | 5.3 → 4.3 (1.2×) | 4.1 → 2.1 (2.0×) | 10.5 → 3.5 (3.0×) | 5.9 → 3.1 (1.9×) | 37.5 → 20.3 (1.8×) |
| 1M | 8.0 → 4.9 (1.6×) | 4.7 → 2.6 (1.8×) | 18.2 → 5.6 (3.2×) | 14.1 → 5.4 (2.6×) | 64.0 → 29.6 (2.2×) |

From n = 100k up, the geometric mean over the five operations is a **2.1×**
speed-up; at n = 10k, where everything fits in cache, it is 1.5×.

### Pure-Python fallback core

Where no native wheel fits (for example CPython 3.13t), the pure-Python core
is used. With `IHEAP_PURE_PYTHON=1` on the same machine, it runs at
pure-Python speed: roughly on par with `pqdict` (0.8–1.5×) and about 10×
slower than the native core. It exists so the library works everywhere, not
for speed.

## Where the time goes

**In Python:** compared with the end-to-end numbers above, the native core
accounts for only 5–20% of each operation. The rest is the interpreter: a
cross-language call costs about 18 ns, and the remaining time goes to Python
method dispatch, `dict` lookups on string keys and object allocation.
`PQDict` adds about 100 ns per call on top of the raw API; that is the cost of
a Python-level `__setitem__` / `popitem` frame. So the core's 2× speed-up
shows up end to end as 1.1–1.4× for `IndexedHeap` and up to 1.3× for
`PQDict`. Removing the remaining overhead would mean moving key storage into
Rust, which contradicts the design goal of keeping the payload in Python.

**In the core:** `pop` is *latency*-bound. The next pop cannot start until
this pop has found the new minimum, because it needs that entry's handle.
Popping from two independent heaps in an interleaved loop gives exactly twice
the throughput, which confirms this. The design therefore aims to shorten the
dependency chain from one root to the next rather than to reduce work:

* A binary heap's sift-down has to compare a level before it knows which
  address to load next. A tournament tree replays a path that is known in
  advance, so the sibling reductions of all levels run in parallel. The serial
  part per level is a single compare-and-select.
* Siblings are read in place *before* their group is written, so loads never
  stall on store-to-load forwarding.
* The root is cached in the struct: one load instead of three dependent ones.

## Optimization log

Every change was measured with the core benchmark above, and each row shows
the measurement that decided it.

| Change | Effect | Outcome |
|---|---|---|
| Inline helper calls on `PQDict` hot paths | −15–25% per op in Python | kept |
| Bulk `PQDict(data)` via `push_many` + C-level `zip`/`dict` | build: 3× → 11–12× vs `pqdict` | kept |
| 4-ary instead of binary heap (first core) | ~1.5× faster than binary | superseded |
| Floyd's bottom-up pop; branch-free 4-way tournament for the minimum child | no change; ~25% slower | rejected |
| 8-ary heap, struct-of-arrays keys, 64-byte-aligned sibling groups, SIMD min-of-8 | update 1.2–1.4×, insert 1.3×, pop ≈ unchanged | superseded |
| Software prefetch of the next level's 8 groups | +8% at 1M, −45% at 10k | rejected |
| SIMD min-of-8 tracking the index through the tournament (vs. bitmask + `ctz`) | pop −7% | superseded |
| Binary tournament tree | pop 1.4–1.8× at ≤100k, slower at 1M (20 levels, 24 MB) | rejected |
| **8-ary tournament tree**, sibling reductions independent of the replayed value | pop 1.5× at 1M; update 1.7× | **adopted** |
| Two-phase updates (re-examine siblings only where the leaf used to win) | delete/insert/update faster; pop slower | kept, with a separate pop path |
| Read sibling groups in place (no 128-byte copies that defeat store forwarding) | pop −15–25%, update −20% | kept |
| Per-level storage, append-only growth (no rebuild on growth), bulk rebuild | build 2×, insert −30% | kept |
| Root cached in the struct | pop −5% at small n | kept |
| NEON vs. scalar `cmov` reductions | NEON 10–20% faster pop | kept (aarch64) |
| Whole-group write-back to help store forwarding | −10% | rejected |
| Unchecked indexing in the hot loop | no change | rejected (kept safe) |
| `ld1`+`uzp` vs `ld2` to split nodes | no change | kept `ld2` (fewer instructions) |

On x86-64 the reductions use the branch-free scalar path, because SSE2 has no
64-bit integer compare. Only aarch64 was measured.

## Reproducing

```bash
uv venv && uv pip install maturin pqdict
maturin develop --release
python benchmarks/bench.py --n 10000 100000 1000000 --markdown
cargo run --release --example bench -p iheap-core
```
