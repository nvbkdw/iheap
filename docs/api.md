# API reference

Everything below is importable from the top-level `iheap` package.

Configuration (see [choosing the implementation](getting-started.md#choosing-the-implementation-yourself)):

| Name | Meaning |
|---|---|
| `iheap.NATIVE` | `True` when the Rust core is the default for numeric priorities |
| `iheap.DEFAULT_BACKEND` | the default backend, from `IHEAP_BACKEND`: `"auto"`, `"native"` or `"python"` |
| `iheap.BACKENDS`, `iheap.PRIORITY_TYPES` | the accepted `backend=` / `priority_type=` values |
| `iheap.heap_class(backend=None)` | the `IndexedHeap` class serving a backend |

`PQDict` also has every method `dict` has. The standard ones it inherits
unchanged (`keys`, `values`, `items`, `__eq__`, ...) behave exactly as they
do for `dict` and are not repeated here.

::: iheap.PQDict

::: iheap.minpq

::: iheap.maxpq

::: iheap.nsmallest

::: iheap.nlargest

::: iheap.IndexedHeap

::: iheap.ObjectHeap
    options:
      members: false
