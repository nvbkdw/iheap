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

Apple M4 Max, CPython 3.13.7, iheap 0.3.0. Each cell is **ns per operation,
lower is better**, with the speed-up over `pqdict` in parentheses. `PQDict`
runs with the default `priority_type="auto"`; `priority_type="float"` skips
the exactness check and saves another 20–50 ns per single update.

=== "n = 10,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 239 (2.2x) | 60 (9.0x) | 153 (5.5x) | 134 (5.5x) | 141 (15.6x) |
    | iheap.IndexedHeap | 58 (8.9x) | 37 (14.5x) | 59 (14.3x) | 71 (10.4x) | 89 (24.7x) |
    | pqdict | 518 | 540 | 843 | 744 | 2,201 |
    | heapq (lazy) | 184 (2.8x) | 109 (5.0x) | 199 (4.2x) | 75 (10.0x) | 383 (5.7x) |

=== "n = 100,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 190 (3.0x) | 58 (10.4x) | 236 (6.3x) | 231 (5.1x) | 226 (16.1x) |
    | iheap.IndexedHeap | 70 (8.1x) | 50 (12.1x) | 92 (16.1x) | 125 (9.4x) | 124 (29.4x) |
    | pqdict | 570 | 603 | 1,475 | 1,177 | 3,637 |
    | heapq (lazy) | 218 (2.6x) | 134 (4.5x) | 411 (3.6x) | 198 (5.9x) | 867 (4.2x) |

=== "n = 1,000,000"

    | implementation | insert | build | update | delete | pop |
    |---|---:|---:|---:|---:|---:|
    | iheap.PQDict | 285 (2.6x) | 97 (9.3x) | 690 (3.8x) | 761 (3.3x) | 739 (11.7x) |
    | iheap.IndexedHeap | 148 (5.0x) | 85 (10.6x) | 423 (6.3x) | 498 (5.0x) | 605 (14.3x) |
    | pqdict | 733 | 903 | 2,646 | 2,511 | 8,645 |
    | heapq (lazy) | 375 (2.0x) | 265 (3.4x) | 885 (3.0x) | 623 (4.0x) | 2,423 (3.6x) |

### Rust core alone

`cargo run --release --example bench -p iheap-core` measures the core with no
Python involved. Each phase repeats until at least 200 ms has been measured,
and the best repetition is reported. The table compares the current 8-ary
tournament tree with the 4-ary binary heap the project started with (ns per
operation, speed-up in parentheses):

| n | insert | build | update | delete | pop |
|---:|---:|---:|---:|---:|---:|
| 10k | 3.0 → 2.9 (1.0×) | 3.5 → 2.3 (1.5×) | 3.2 → 1.9 (1.7×) | 3.9 → 2.5 (1.6×) | 25.2 → 17.3 (1.5×) |
| 100k | 5.3 → 3.9 (1.4×) | 4.1 → 2.2 (1.9×) | 10.5 → 2.9 (3.6×) | 5.9 → 3.0 (2.0×) | 37.5 → 20.6 (1.8×) |
| 1M | 8.0 → 4.9 (1.6×) | 4.7 → 2.6 (1.8×) | 18.2 → 5.3 (3.4×) | 14.1 → 5.8 (2.4×) | 64.0 → 29.2 (2.2×) |

From n = 100k up, the geometric mean over the five operations is a **2.1×**
speed-up; at n = 10k, where everything fits in cache, it is 1.5×.

### Pure-Python fallback core

Where no native wheel fits (for example CPython 3.13t or PyPy), the
pure-Python core is used. With `IHEAP_BACKEND=python` on the same machine, it
runs at pure-Python speed: roughly on par with `pqdict` (0.7–1.7×) and about
10× slower than the native core. It exists so the library works everywhere,
not for speed.

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
| Rebuild and `clear` proportional to `handle_bound` instead of capacity | small batches on a large allocation: milliseconds → microseconds | kept |
| `panic = "unwind"` instead of `"abort"` | +2–3 ns per call | kept: a bug raises an exception instead of killing the interpreter |
| `int`-like handles and any-iterable batches in the binding | +~2 ns per call | kept: identical argument rules on every core |
| `priority_type="auto"` exactness check in `PQDict` | +20–50 ns per single update, +7–15 ns per item in bulk | kept as the default for exact ordering; `"float"` skips it |
| Plan-then-apply sifts (exception safety) in the pure float core | ~40% slower fallback | moved to `ObjectHeap` only, where comparisons can raise |

On x86-64 the reductions use the branch-free scalar path, because SSE2 has no
64-bit integer compare. Only aarch64 was measured.

## Reproducing

```bash
uv venv && uv pip install maturin pqdict
maturin develop --release
python benchmarks/bench.py --n 10000 100000 1000000 --markdown
cargo run --release --example bench -p iheap-core
```
