# Design and implementation

This page explains how `iheap` works internally, and why each decision was
made. It covers every layer, from the bit-level key encoding to the CPU
pipeline effects that shaped the core. The measurements behind each decision
are on the [benchmark](benchmark.md) page.

## Goals and constraints

The job is an **indexed priority queue**: a priority queue that, besides
`push` and `pop`, can change the priority of any entry or remove any entry in
`O(log n)`. That's what Dijkstra/A\*, schedulers and event simulations need,
and what `heapq` can't do.

These constraints shaped the design:

1. **The payload stays in Python.** Only priorities and integer handles cross
   the Python ↔ Rust boundary. Rust never holds or calls a Python object.
   Keys and values are never copied, converted or pickled.
2. **Priorities are `float`s.** Native comparisons are the whole point of a
   native core. Any Python object that `float()` accepts works. NaN, which has
   no order, is rejected.
3. **Speed first, without `unsafe` shortcuts.** The core is safe Rust except
   for SIMD intrinsics, each with a `SAFETY` comment. Every optimization was
   measured, and the ones that didn't pay were removed.

| Operation | Complexity | Notes |
|---|---|---|
| `peek` | `O(1)` | root is cached in the struct |
| `push` | `O(log n)` worst, `O(1)` typical | early exit |
| `update` / `remove` | `O(log n)` worst, `O(1)` typical | early exit unless the entry was winning |
| `pop` | `O(log n)` | 8-ary: ⌈log₈ n⌉ levels |
| `push_many` / `update_many` | `O(k log n)` or `O(n)` | rebuilds when `k · log₂ n ≥ n` |
| memory | ≈ 10.3 bytes per handle | vs. 24 for a classic indexed binary heap |

## Architecture

```
            Python  (python/iheap/_pqdict.py)            Rust  (crates/iheap-core)
 ┌────────────────────────────────────────────┐   ┌──────────────────────────────────┐
 │ PQDict                                     │   │ IndexedHeap                      │
 │   _handles: dict   key    -> handle        │   │   leaves:  [LeafGroup]   (64 B)  │
 │   _keys:    list   handle -> key           │──▶│   levels:  [[NodeGroup]] (128 B) │
 │   _values:  list   handle -> value         │   │   root:    Node                  │
 └────────────────────────────────────────────┘   │   free:    [Handle]              │
          f64 priorities in, u32 handles out ▲    └──────────────────────────────────┘
                                    crates/iheap-py: PyO3 binding, one Mutex per heap
```

There are three layers, each testable on its own:

- **`iheap-core`**: pure Rust, no PyO3 dependency. It contains the data
  structure and nothing else, so `cargo test` runs without a Python
  interpreter.
- **`iheap-py`**: a thin PyO3 binding. It converts types and errors, and
  guards each heap with a lock for free-threaded Python.
- **`python/iheap`**: the `PQDict` mapping API. It owns all payload storage
  and maps keys to handles.

## Handles

A **handle** is a `u32` naming one entry for as long as it lives.

- **Dense and recycled.** Fresh handles are issued in order `0, 1, 2, …`.
  Released handles go onto a LIFO free list and are reused first. All live
  handles are below `handle_bound`, the high-water mark. A caller can
  therefore store per-entry data in a plain list indexed by handle, and that
  list never grows past the peak queue size.
- **Fresh heaps are predictable.** After construction or `clear()`,
  `push_many(ps)` returns exactly `[0, 1, …, len(ps) − 1]`. The Python layer
  relies on this to bulk-load without a per-item loop.
- **The handle is the address.** In the tournament tree below, handle `h` is
  leaf `h`. There is no handle → position map to keep up to date, which is
  one store fewer per moved entry than in an indexed binary heap.

## Key encoding

Priorities are stored as **order-preserving `u64` keys** rather than `f64`:

```rust
fn encode_min(p: f64) -> u64 {
    let bits = p.to_bits();
    if bits >> 63 == 1 { !bits }          // negative: flip all bits
    else { bits | 1 << 63 }               // positive: set the sign bit
}
```

For non-NaN floats, unsigned integer order then matches float order. This
gives three useful properties:

- **Integer compares.** They are cheap, and NEON has a native 64-bit unsigned
  compare (`cmhi`).
- **A total order with exact round-trips.** `-0.0` and `+0.0` get distinct
  keys, with `-0.0` sorting first, and both decode to the exact bits that went
  in.
- **A sentinel that can't collide.** A vacant leaf holds `VACANT = u64::MAX`.
  Only a NaN encodes to it, and NaN is rejected, so no real priority can ever
  equal the sentinel. It also loses every comparison, including to `+∞`.

A **max-queue** stores the bitwise complement `!encode_min(p)`, which reverses
the order. The comparison code is identical for both orders: no branch, no
comparator.

## Memory layout: an 8-ary tournament tree

The core is a **tournament (winner) tree**, not a heap. In a heap, entries
move around to keep the heap order. In a tournament tree, entries stay in
their leaves, and every internal node records which leaf wins its subtree:

```
level 1 (root)               R = winner(n0 … n7)
                ┌──────┬─────┴───┬─ … ─┐
level 0       n0      n1   …    n7          n_i = winner of leaf group i
           ┌──┴──┐  ┌──┴──┐
leaves     h0 … h7  h8 … h15  …   h56 … h63
```

- **`Node = { key: u64, leaf: u64 }`**, 16 bytes. The leaf id fits in the low
  half. Using two full words means writing a node is a single 16-byte store.
- **Leaves are grouped by 8** into a `LeafGroup` (8 × 8 B = one 64-byte cache
  line, 64-byte aligned). Leaf `h` is `leaves[h / 8][h % 8]`.
- **Internal nodes are grouped by 8** into a `NodeGroup` (8 × 16 B = 128
  bytes, 128-byte aligned). That is exactly one cache line on Apple silicon
  and two adjacent lines on x86, which the adjacent-line prefetcher fetches
  together.
- **On level `l`, the node above leaf `h` is `h >> 3(l + 1)`.** The whole path
  from leaf to root is known before any key is read, and it doesn't depend on
  how big the tree is.
- **`levels[l]` is a separate `Vec` per level**, so growing only appends: new
  leaves cover only vacant slots, so `EMPTY` is already their correct winner.
  When the top level gains a second group, a new level is added above it, and
  its first node is the old root.
- **The root is cached in the struct**, outside `levels`. Every `pop` starts
  by reading the root, and this makes that a single load from the struct
  instead of three dependent loads (outer `Vec` → level `Vec` → node).

Depth is `⌈log₈ n⌉`: 4 internal levels plus the root at 10k entries, and 6
plus the root at 1M. Memory is 8 bytes per leaf plus 16 bytes per 8 children
on each level above, about **10.3 bytes per handle** at every size. A
classic indexed binary heap needs about 24.

## Operations

All mutations write one leaf and then restore the winners on that leaf's path
to the root. There are two replay routines, one per access pattern.

### `pop`: replaying the winner's path

The root names the winning leaf `w`. Popping writes `VACANT` into `w` and
recomputes every node on `w`'s path. `w` won every one of those nodes, so all
of them change, and each must be recomputed from its *other* children:

```rust
fn replace_winner(&mut self, key: u64) {          // key = VACANT for pop
    let w = self.root.handle();
    let (mut index, lane) = (w / 8, w % 8);
    let others = leaf_group_winner(&self.leaves[index], index, lane); // read first
    self.leaves[index].0[lane] = key;                                 // then write
    let mut best = winner(Node::new(key, w), others);
    for level in &mut self.levels {
        let (g, lane) = (index / 8, index % 8);
        let others = group_winner(&level[g], lane);   // 7 siblings, excluding `lane`
        level[g].0[lane] = best;
        best = winner(best, others);                  // the only serial step
        index = g;
    }
    self.root = best;
}
```

The key property is that **`others` on each level doesn't depend on `best`**.
It depends only on the path (known from `w`) and on sibling nodes that this
pop doesn't modify. An out-of-order CPU therefore runs the loads and 7-way
reductions of all levels in parallel. The only serial work, the chain that
`best` threads through, is one compare-and-select per level.

A binary heap's sift-down is the opposite. It can't know which child to
descend into, and so which address to load next, until it has compared the
current level. Every level is a full load → compare → branch round trip.

`push_pop(p)` reuses the same routine. If `p` would not win, the heap is left
unchanged. Otherwise the current winner's leaf gets key `p` (its handle is
reassigned) and its path is replayed: one pass, as in `heapq.heappushpop`.

### `push`, `update`, `remove`: a two-phase replay with early exit

These write an arbitrary leaf `h`. Its path splits into two parts:

- **Phase A: nodes that `h` used to win.** Their stored winner equals `h`'s
  *old* value. Each must be recomputed from its other children, exactly as in
  `pop`. That's the only way to find the new winner when `h` got worse.
- **Phase B: nodes above that, where a sibling had won.** The siblings are
  unchanged, so the new winner is just `winner(stored, new)`, a single
  compare. As soon as a node comes out unchanged, nothing above it can
  change, so the replay stops.

```rust
// phase A, while the node's stored winner is our old value
new = winner(new, others_excluding_us);
if new == old { return; }                         // nothing changed
// read the parent *before* writing this node; reduce siblings only if needed
let parent_was_ours = self.stored(level + 1, g) == old;
if parent_was_ours { others = group_winner(&self.levels[level][g], lane); }
self.levels[level][g].0[lane] = new;

// phase B, once a sibling had won: one comparison per level, early exit
let value = winner(stored, new);
if value == stored { return; }
```

**Why phase B never needs phase A again.** In phase B, the child we come from
holds `new = winner(stored_child, new_below)`, and `new` differs from
`stored_child` only if it is *strictly better*. A child that only improves
can be compared against the parent's stored winner directly: if the parent's
winner was that child's old value, the improved value still wins. So the cheap
rule stays correct all the way up.

**Why this is fast in practice.**

- A newly pushed key, a decrease-key, or any change to an entry that wasn't
  winning anything goes straight to phase B and usually exits after one or two
  comparisons.
- Removing a non-winning entry exits after the first compare: `VACANT` never
  beats the stored winner.
- Only raising the priority of, or removing, an entry that currently wins
  part of its path pays for the sibling reductions, and only on those levels.

### Bulk operations

`push_many(ps)` and `update_many(hs, ps)` first validate their *entire* input
(no NaN, valid handles, equal lengths), so a failure changes nothing. Then
they choose between `k` individual replays (`O(k log n)`) and writing all `k`
leaves followed by one bottom-up rebuild (`O(n)`). The rule is
`k · ⌊log₂ n⌋ ≥ n`. The rebuild computes each level from the one below, one
group reduction per node. `PQDict(data)` uses this path with a single native
call.

### `clear`

`clear` resets every leaf to `VACANT` and every node to `EMPTY`, keeping the
allocations. Handle numbering then restarts at 0.

## Micro-architecture

### `pop` is latency-bound

Consecutive pops form a dependency chain. Pop *k + 1* can't even compute its
first address until pop *k* has produced the new root, because the root
names the leaf to start from. Two experiments confirm it:

- Popping from **two independent heaps in an interleaved loop gives exactly 2×
  the throughput** of popping from one, so the CPU has plenty of spare
  execution capacity. The cost is waiting, not work.
- Pop cost grows by only ~2–4 ns per level but has a fixed ~8 ns floor, even
  for an 8-element heap.

Every core decision therefore aims at shortening the chain from one root to
the next, rather than at reducing instruction count:

| Decision | Effect on the chain |
|---|---|
| Tournament tree instead of a heap | path known in advance; per level, the chain is one compare-and-select instead of load → compare → branch |
| Fanout 8 | ⌈log₈ n⌉ levels instead of ⌈log₂ n⌉ (6 instead of 20 at 1M) |
| One aligned 64/128-byte group per level | at most one cache miss per level, and all levels miss in parallel |
| Root cached in the struct | first load of a pop: one load instead of three dependent loads |
| Siblings read *before* the slot is written | see store forwarding below |

### Store-to-load forwarding

A load that overlaps an earlier store which hasn't reached the cache yet is
served from the store buffer, but only if the load is fully contained in a
single store. A wide group load overlapping a narrower recent store can't be
forwarded, so the load waits until the store commits.

The replay therefore always **reads a group's siblings before writing our
slot in that group**. It also **never copies a group into a local variable**.
A 128-byte copy is spilled to the stack, so the next load reads it back
through a mismatched store. Removing such copies cut pop time by 15–25%.

### SIMD reductions

On aarch64, `group_winner` computes the 7-sibling winner with NEON. NEON is
part of the baseline ISA, so it needs no runtime feature detection and is
safe in portable wheels:

```text
vld2q_u64 ×4        8 nodes → 4 key vectors + 4 leaf vectors (de-interleaved)
key |= (lane == skip) ? ~0 : 0      exclude our own lane by forcing it to VACANT
2 rounds of  b_wins = vcltq_u64(kb, ka)
             key    = vbslq(b_wins, kb, ka);  leaf = vbslq(b_wins, lb, la)
1 scalar select across the two remaining lanes (select_unpredictable → csel)
```

The lane index or leaf id rides along with each key through the tournament,
so no separate pass is needed to find *which* lane won. Masking with `OR`
turns "skip our lane" into a single instruction per vector. Leaf groups use
the same tournament with `vld1q`, carrying lane indices. On other
architectures, a branch-free scalar tournament built from
`std::hint::select_unpredictable` (`cmov`/`csel`) is used. SSE2 has no 64-bit
integer compare, and the scalar version is only 10–20% slower than NEON.

### What did not help

Some reasonable-sounding ideas were measured and rejected:

- Unchecked indexing in the hot loop made no difference: bounds checks are
  off the critical path, so the code stays safe.
- Writing back whole groups to help forwarding was 10% slower.
- `ld1` + `uzp` loads instead of `ld2` made no difference.
- Software prefetch of the next level was 45% slower when the data fits in
  cache.

The [optimization log](benchmark.md#optimization-log) has the numbers.

## The Python layer

`PQDict` is a `MutableMapping` that keeps three structures in step with the
native heap:

```text
_handles : dict[key, handle]      key lookup, and insertion order for iteration
_keys    : list, index = handle   reverse lookup when a pop returns a handle
_values  : list, index = handle   the payload; priority = key(value) or value
```

- **Hot paths are inlined.** `__setitem__`, `__delitem__` and `popitem` don't
  call helper methods: a Python call frame costs more than the native
  operation itself.
- **Bulk load on an empty queue** (the constructor, or `update()` on an empty
  queue) builds `_keys` and `_values` directly with `list(...)`. It relies on
  fresh handles being `0..n − 1`, which turns `_handles` into
  `dict(zip(keys, range(n)))`: C-level loops plus one native call.
- **`update()` is atomic.** New keys go through one `push_many` and existing
  keys through one `update_many`. If the second call fails, the first is
  rolled back.
- **Payload references are released immediately.** When an entry leaves the
  queue, its list slots are set to `None`, so the value can be garbage
  collected.
- **Some operations need no native call:** `pushpopitem` uses the native
  `push_pop` and reuses the popped handle for the new key, while
  `replace_key` and `swap_priority` only re-wire `_handles` and `_keys`.

## Concurrency

The binding wraps each core heap in a `std::sync::Mutex` (a `frozen` PyO3
class), and the module is declared `gil_used = false`:

- Concurrent calls on one `IndexedHeap` from several threads are
  **serialized**, as for built-in `list` operations. The alternative,
  PyO3's per-object borrow flag, would raise `RuntimeError: Already borrowed`
  instead.
- The lock is never held while Python code runs, because PyO3 converts all
  arguments before the method body takes it. So it can't deadlock against
  the interpreter. On GIL builds it costs nothing measurable.
- On free-threaded CPython 3.14t, importing `iheap` keeps the GIL
  disabled. Those interpreters can't load `abi3` wheels, so CI builds
  dedicated `cp314t` wheels.
- **`PQDict` is not safe for concurrent mutation.** Each of its operations
  takes several steps across a `dict`, two `list`s and the native heap. Guard
  a shared `PQDict` with a lock.

## Alternatives considered

| Design | Why not |
|---|---|
| Indexed binary or 4-ary heap (the first version) | Sift-down is a chain of load → compare → branch per level; a position map is needed; 2–3× slower than the final design on update and pop |
| 8-ary heap, struct-of-arrays keys, aligned groups, SIMD min-of-8 | Faster updates, but pop unchanged: the SIMD reduction sits on the dependent chain between levels |
| Binary tournament tree | Fast at ≤ 100k entries, but 20 levels and 24 bytes per entry at 1M; slower than the heap at 1M |
| Pairing / Fibonacci heaps | Good asymptotics, but pointer-chasing nodes and an allocation per entry; poor constants in practice |
| Radix heaps, bucket queues | Need integer or monotone priorities; not general |
| Generic Python priorities (`__lt__`) | Every comparison would call back into Python and lose most of the native speed |
| Keys and values stored in Rust | Would remove the ~100 ns `PQDict` overhead, but breaks the "payload stays in Python" constraint |

## How it is tested

- **Randomized differential test** (`crates/iheap-core/tests/model.rs`).
  Thousands of random push, pop, update, remove, bulk, `push_pop` and `clear`
  operations are checked against a `HashMap` model in both orders, including
  `±0`, `±∞` and ties.
- **Structural invariant checker** (`assert_invariants`). Every node must
  equal the winner of its children. It's exercised at sizes straddling every
  level boundary, for both incremental growth and bulk rebuild.
- **Unit tests** for the key encoding (order and exact round-trips) and for
  the SIMD reductions against a scalar reference, with heavy ties.
- **Python tests:** a `dict` parity test (results, iteration order,
  `reversed`), a differential test against `pqdict`, doctests, every code
  block in these docs, and an 8-thread stress test that runs on free-threaded
  CPython in CI.
