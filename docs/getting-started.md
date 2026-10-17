# Getting started

## Installation

```bash
pip install iheap
```

Wheels are published for CPython 3.10+ on Linux (x86_64/aarch64, glibc and
musl), macOS (x86_64/arm64) and Windows (x64), including the free-threaded
(no-GIL) build of 3.14. On other platforms pip builds from source,
which requires a [Rust toolchain](https://rustup.rs).

## The basics

A [`PQDict`][iheap.PQDict] maps **keys** to **values**. Each value is also the
item's **priority** (see [custom priorities](#custom-priorities) for how to
change that). The item with the smallest priority is always on *top*.

```python
from iheap import PQDict

pq = PQDict({"write docs": 2, "fix bug": 1})
pq["release"] = 3          # insert
pq["fix bug"] = 5          # change a priority: O(log n)
del pq["release"]          # remove any item: O(log n)

assert pq.top() == "write docs"           # peek at the top key
assert pq.topitem() == ("write docs", 2)  # ... or the top item
assert pq.popitem() == ("write docs", 2)  # remove the top item
assert len(pq) == 1
```

To consume the queue in priority order, use the destructive iterators:

```python
pq = PQDict({"c": 3, "a": 1, "b": 2})
assert list(pq.popkeys()) == ["a", "b", "c"]
assert len(pq) == 0
```

[`popvalues()`][iheap.PQDict.popvalues] and
[`popitems()`][iheap.PQDict.popitems] work the same way.

## Max-queues

Pass `reverse=True`, or use [`maxpq`][iheap.maxpq], to put the largest
priority on top:

```python
from iheap import PQDict, maxpq, minpq

scores = maxpq(alice=90, bob=72, carol=85)
assert scores.top() == "alice"

assert PQDict({"x": 1, "y": 2}, reverse=True).top() == "y"
assert minpq(x=1, y=2).top() == "x"
```

## Custom priorities

Use `key=` when the priority comes from part of the value, as with
`sorted(key=...)`. The value is stored unchanged; only `key(value)` is sent to
the native heap.

```python
tasks = PQDict(key=lambda task: task["deadline"])
tasks["deploy"] = {"deadline": 17.5, "owner": "ops"}
tasks["review"] = {"deadline": 9.0, "owner": "dev"}

key, task = tasks.popitem()
assert key == "review" and task["owner"] == "dev"
```

If you change a value **in place** in a way that changes its priority, tell the
queue with [`heapify`][iheap.PQDict.heapify]:

```python
tasks["deploy"]["deadline"] = 1.0   # mutated in place: the heap doesn't know
tasks.heapify("deploy")             # re-prioritize one item in O(log n)
tasks.heapify()                     # ... or every item in O(n)
```

## It is a full `dict`

Every `dict` operation works, and iteration follows **insertion order**,
exactly like `dict`:

```python
pq = PQDict({"b": 2, "a": 1})
pq.setdefault("c", 3)
assert pq.get("zzz", 0) == 0
assert list(pq) == ["b", "a", "c"]          # insertion order, not priority order
assert list(reversed(pq)) == ["c", "a", "b"]
assert pq == {"a": 1, "b": 2, "c": 3}       # compares equal to any mapping

merged = pq | {"d": 0}                      # returns a new PQDict
assert merged.top() == "d"
pq |= {"a": 10}
assert pq.top() == "b"
```

!!! note "The one difference from `dict`"
    `popitem()` removes the **top-priority** item, not the last inserted one.
    `pop()` without arguments removes the top item and returns its key;
    `pop(key[, default])` behaves exactly like `dict.pop`.

## More priority-queue operations

```python
pq = PQDict({"a": 5, "b": 7})

pq.additem("c", 6)                 # strict insert: KeyError if "c" exists
pq.updateitem("c", 8)              # strict update: KeyError if "c" is missing

# Push and pop in one pass (like heapq.heappushpop):
assert pq.pushpopitem("d", 1) == ("d", 1)    # would be on top: returned as-is
assert pq.pushpopitem("e", 6) == ("a", 5)    # "a" popped, "e" inserted

pq.replace_key("e", "E")           # rename a key, keeping value and priority
pq.swap_priority("b", "c")         # exchange two items' values in O(1)
assert pq == {"b": 8, "c": 7, "E": 6}
```

For one-off selection, [`nsmallest`][iheap.nsmallest] and
[`nlargest`][iheap.nlargest] run in native code:

```python
from iheap import nlargest, nsmallest

prices = {"apple": 1.2, "pear": 0.8, "kiwi": 2.5, "fig": 3.1}
assert nsmallest(2, prices) == ["pear", "apple"]
assert nlargest(1, prices) == ["fig"]
```

## Example: Dijkstra's shortest paths

Decrease-key is what makes an indexed heap the natural fit here:

```python
from iheap import PQDict

def dijkstra(graph, source):
    dist = {}
    frontier = PQDict({source: 0.0})
    while frontier:
        node, d = frontier.popitem()
        dist[node] = d
        for neighbour, weight in graph[node].items():
            if neighbour in dist:
                continue
            candidate = d + weight
            if candidate < frontier.get(neighbour, float("inf")):
                frontier[neighbour] = candidate     # insert or decrease-key
    return dist

graph = {
    "a": {"b": 7, "c": 9, "f": 14},
    "b": {"a": 7, "c": 10, "d": 15},
    "c": {"a": 9, "b": 10, "d": 11, "f": 2},
    "d": {"b": 15, "c": 11, "e": 6},
    "e": {"d": 6, "f": 9},
    "f": {"a": 14, "c": 2, "e": 9},
}
assert dijkstra(graph, "a") == {"a": 0, "b": 7, "c": 9, "f": 11, "d": 20, "e": 20}
```

## The low-level `IndexedHeap`

[`IndexedHeap`][iheap.IndexedHeap] is the native core. It stores only float
priorities and gives back integer **handles**. Use it when you already have
your own storage, such as numpy arrays or list-based graph nodes, and want
to skip the `PQDict` layer, which costs roughly 100 ns per operation.

```python
from iheap import IndexedHeap

heap = IndexedHeap()                       # IndexedHeap(reverse=True) for max
names = {}                                 # your own handle -> data storage

for name, priority in [("x", 3.0), ("y", 1.0), ("z", 2.0)]:
    names[heap.push(priority)] = name

handle, priority = heap.peek()
assert names[handle] == "y" and priority == 1.0

z = next(h for h, n in names.items() if n == "z")
heap.update(z, 0.5)                        # O(log n), returns the old priority
assert names[heap.pop()[0]] == "z"

handles = heap.push_many([9.0, 8.0])       # bulk insert, O(n) heapify if large
heap.update_many(handles, [0.1, 0.2])      # bulk update
assert [p for _, p in heap.pop_many(2)] == [0.1, 0.2]
```

A handle is valid until its entry is popped or removed. After that it is
**recycled** by later pushes. Handles are always below
[`handle_bound`][iheap.IndexedHeap.handle_bound], so a list indexed by handle
is the fastest way to store data alongside them.

## Things to know

- **Priorities are `float`s.** Any value that `float()` accepts works (`int`,
  `float`, `Decimal`, `Fraction`, numpy scalars). Integers are exact up to
  2⁵³. NaN raises `ValueError`, and non-numeric priorities raise `TypeError`.
  The queue is left unchanged in both cases.
- **Ties** are popped in unspecified order. To get FIFO order among equal
  priorities, use a priority like `primary + counter * 1e-12`, or encode the
  tie-breaker in the priority itself.
- **Bulk operations are faster.** `PQDict(data)` and `pq.update(many_items)`
  build in `O(n)` with one native call, which beats inserting in a loop.
- **Thread safety.** [`IndexedHeap`][iheap.IndexedHeap] can be shared between
  threads. Concurrent calls on one heap are serialized by an internal lock,
  also on free-threaded Python, where importing `iheap` keeps the GIL
  disabled. Compound sequences, such as `peek` then `update`, still need your
  own lock to be atomic. **`PQDict` is not safe for concurrent mutation**: each
  of its operations takes several steps across Python and the native heap.
  Guard a `PQDict` that several threads modify with a `threading.Lock`; that
  is required on free-threaded Python and recommended everywhere.
