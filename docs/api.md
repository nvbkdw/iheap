# API reference

Everything below is importable from the top-level `iheap` package.

`iheap.NATIVE` is `True` when the Rust core is in use, and `False` when the
pure-Python fallback with the same API is (see
[installation](getting-started.md#installation)).

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
