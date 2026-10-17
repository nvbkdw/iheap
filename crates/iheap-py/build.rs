fn main() {
    // Exposes `cfg(Py_GIL_DISABLED)`, which selects the free-threaded core.
    pyo3_build_config::use_pyo3_cfgs();
}
