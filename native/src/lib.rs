mod extract;
mod limits;
mod scope;
mod tree;

use pyo3::{exceptions::PyValueError, prelude::*};

#[pyfunction(name = "extract")]
fn extract_facts(
    py: Python<'_>,
    source: String,
    module: String,
    file: String,
    builtins: Vec<String>,
) -> PyResult<String> {
    py.detach(move || {
        limits::check(&source)?;
        // Parsing uses Ruff's stack growth; this stack also covers AST destruction.
        std::panic::catch_unwind(|| {
            stacker::grow(32 * 1024 * 1024, || {
                extract::extract(&source, &module, &file, builtins.into_iter().collect())
            })
        })
        .unwrap_or_else(|_| Err("extraction: native parser panicked".to_owned()))
    })
    .map_err(PyValueError::new_err)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_facts, m)?)?;
    Ok(())
}
