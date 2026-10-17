# Getting started

## Installation

```bash
pip install iheap
```

iheap supports CPython 3.11 and later, and every platform Python runs on.
pip picks the best implementation for your interpreter automatically:

| Interpreter | Wheel | Core |
|---|---|---|
| CPython 3.11+ (regular, with the GIL) | `cp311-abi3` | native Rust, GIL-specialized: no locking at all |
| CPython 3.14t (free-threaded) | `cp314t` | native Rust with a mutex per heap |
| CPython 3.15t and later (free-threaded) | `cp315-abi3t` | native Rust with a mutex per heap |
| CPython 3.13t, PyPy, GraalPy, Pyodide, other platforms | `py3-none-any` | pure Python, same API |

Native wheels cover:

- **Linux** (glibc): x86_64, i686, aarch64, armv7, ppc64le, s390x;
- **Linux** (musl): x86_64, i686, aarch64, armv7;
- **macOS**: x86_64 and arm64;
- **Windows**: x64, x86 and arm64.

Free-threaded wheels are built for the 64-bit targets. The pure-Python core
behaves identically, but runs at pure-Python speed, comparable to `pqdict`.
To build the native core from source on a platform without a native wheel,
use `pip install --no-binary iheap iheap`, which needs Rust 1.88 or later.

### Choosing the implementation yourself

The defaults are right for almost everyone. When you need control:

```python
import iheap
from iheap import PQDict

print(iheap.NATIVE)                # True when the Rust core is the default
pq = PQDict({"a": 1}, backend="python")
assert pq.core == "python"         # "native", "python" or "object"
```

| Setting | Values | Effect |
|---|---|---|
| `IHEAP_BACKEND` environment variable | `auto` (default), `native`, `python` | process-wide default for numeric priorities |
| `backend=` argument (`PQDict`, `nsmallest`, `nlargest`) | `None` (use the default), `"auto"`, `"native"`, `"python"` | per queue; `"native"` raises `ImportError` if no native core is installed |
| `priority_type=` argument | `"auto"` (default), `"float"`, `"object"` | which priorities a queue accepts; see [String and other priorities](#string-and-other-priorities) |

The older `IHEAP_PURE_PYTHON=1` still works and means `IHEAP_BACKEND=python`.
An invalid `IHEAP_BACKEND` value makes `import iheap` fail with a clear error
rather than silently picking a core.

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

Pass `key=` to compute an item's priority from its **key and value**. The
function is called as `key(item_key, value)`. The value is stored unchanged;
only the returned priority is sent to the native heap.

```python
tasks = PQDict(key=lambda name, task: task["deadline"])
tasks["deploy"] = {"deadline": 17.5, "owner": "ops"}
tasks["review"] = {"deadline": 9.0, "owner": "dev"}

name, task = tasks.popitem()
assert name == "review" and task["owner"] == "dev"
```

The key can take part in the priority too, for example to break ties or to
weight items:

```python
# Shorter names first among equal deadlines.
jobs = PQDict(key=lambda name, deadline: deadline + len(name) / 1000)
jobs.update({"backup": 3.0, "db": 3.0, "etl": 1.0})
assert list(jobs.popkeys()) == ["etl", "db", "backup"]
```

!!! note "Difference from `pqdict`"
    `pqdict`'s key function receives only the value. To port such code,
    change `key=f` to `key=lambda k, v: f(v)`. When a key function is set,
    [`replace_key`][iheap.PQDict.replace_key] and
    [`swap_priority`][iheap.PQDict.swap_priority] recompute the affected
    priorities, because the key is an input. The same `(key, value)` function
    form is used by [`nsmallest`][iheap.nsmallest] and
    [`nlargest`][iheap.nlargest].

If you change a value **in place** in a way that changes its priority, tell the
queue with [`heapify`][iheap.PQDict.heapify]:

```python
tasks["deploy"]["deadline"] = 1.0   # mutated in place: the heap doesn't know
tasks.heapify("deploy")             # re-prioritize one item in O(log n)
tasks.heapify()                     # ... or every item in O(n)
```

## String and other priorities

Priorities can be any objects that support `<`: numbers, strings, tuples,
dates, exact `Decimal`s, integers of any size. You don't need to say which:

```python
from datetime import date

words = PQDict({"x": "pear", "y": "apple", "z": "fig"})
assert list(words.popkeys()) == ["y", "z", "x"]

due = PQDict(reverse=True)                            # latest date first
due.update({"tax": date(2026, 4, 15), "rent": date(2026, 11, 1)})
assert due.top() == "rent"

# Tuples make ties deterministic: equal priority -> smaller sequence number first.
fifo = PQDict(key=lambda name, item: (item[0], item[1]))
fifo.update({"b": (1, 0), "a": (1, 1), "c": (0, 2)})
assert list(fifo.popkeys()) == ["c", "b", "a"]
```

How it works: with the default `priority_type="auto"`, a queue runs on the
fast Rust core as long as every priority is a number that a `float`
represents exactly. The first time a priority isn't (a `str`, a `tuple`, a
`date`, an `int` beyond 2⁵³, an inexact `Decimal`), the queue moves to the
pure-Python object core, once, in `O(n)`, keeping all items. Order is then
exact, but speed is pure-Python, about the same as `pqdict`. `pq.core`
reports which core holds a queue.

```python
big = PQDict({"a": 2**60, "b": 2**60 + 1})   # 2**60 + 1 is not an exact float
assert big.core == "object" and big.top() == "a"
```

To pin the behavior, pass `priority_type="float"` (numeric core only;
anything else raises `TypeError`; this also skips the exactness check, for
maximum speed) or `priority_type="object"` (always the object core, which
[`ObjectHeap`][iheap.ObjectHeap] exposes directly). Priorities in one queue
must be comparable with each other. A priority that isn't (say, a `str` in a
queue of numbers) raises `TypeError` and leaves the queue unchanged.

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

For one-off selection, use [`nsmallest`][iheap.nsmallest] and
[`nlargest`][iheap.nlargest]. They run in native code for numeric priorities:

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

- **Priorities.** Numbers of any kind work (`int`, `float`, `Decimal`,
  `Fraction`, numpy scalars), and so does anything else that supports `<`
  (see [String and other priorities](#string-and-other-priorities)). Order is
  always exact: big integers and inexact decimals move a queue to the object
  core rather than being rounded. NaN raises `ValueError`, and incomparable
  priorities raise `TypeError`; the queue is left unchanged in both cases.
- **Ties** are popped in unspecified order. To get FIFO order among equal
  priorities, use a tuple priority such as `(priority, sequence_number)`.
- **Bulk operations are faster.** `PQDict(data)` and `pq.update(many_items)`
  build in `O(n)` with one native call, which beats inserting in a loop.
- **Every operation is atomic.** If an operation raises, whether from a bad
  priority, a key function or a comparison, the queue is unchanged.
- **Thread safety.** [`IndexedHeap`][iheap.IndexedHeap] and
  [`ObjectHeap`][iheap.ObjectHeap] can be shared between threads: concurrent
  calls on one heap are serialized (by the GIL on regular builds; by a lock
  per heap on free-threaded Python and in the pure-Python cores). On
  free-threaded Python, importing `iheap` keeps the GIL disabled. Compound
  sequences, such as `peek` then `update`, still need your own lock to be
  atomic. **`PQDict` is not safe for concurrent mutation**: each of its
  operations takes several steps across Python and the heap. Guard a `PQDict`
  that several threads modify with a `threading.Lock`; that is required on
  free-threaded Python and recommended everywhere.
- **Pickling and copying** work for queues and heaps; heaps restore exactly,
  including which handles will be reused next.
