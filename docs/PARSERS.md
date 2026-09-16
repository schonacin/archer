# Parser backends

Archer defaults to `--parser rust`. Use `--parser libcst` to compare with the original implementation. The same option applies to scan, render, context, diff, and check, including both sides of Git comparisons. `[tool.archer] parser = "libcst"` sets the repository default; the CLI overrides it. Python callers use `scan(..., parser="libcst")` or `scan_sources(..., parser="libcst")`. Loading a saved graph does not run either parser.

Install `archer[libcst]` (or `archer[all]`) to enable LibCST. The default path never imports LibCST. An unavailable backend fails explicitly rather than silently changing behavior.

## Contract

`scan/parser.py` selects an extractor with signature `extract(source, module, file) -> ParsedModuleFacts`. `scan/facts.py` owns the contract: declaration nodes, containment edges, pending references, exports, attributes, bindings, and rebindings. No syntax-tree or provider objects cross this boundary. The scanner decodes source encodings, merges successful files atomically, constructs package hierarchy, and invokes the shared repository resolver.

The Rust implementation lives in `native/`: Ruff parses the source, an iterative traversal records lexical scopes and bindings, and extraction produces plain facts. PyO3 releases the GIL during native work and returns one JSON payload per file. `rust_backend.py` converts those facts to the shared Python representation. The LibCST adapter implements the same contract with its existing metadata and visitor behavior.

The resolver stays in Python. It follows re-exports, searches class members and bases, applies annotation/constructor inference, and assigns confidence to graph edges. It does not depend on either backend.

## Compatibility

Successful scans are compared at the canonical IR level, including declaration IDs, duplicate-definition numbering, Unicode character columns, references, fingerprints, and unresolved-node IDs. Tests cover lexical shadowing, assignment order, conditional imports, receivers, annotations, decorators, lambdas/comprehensions, encodings, and Archer's own source tree. A saved graph fixture produced by the pre-migration implementation verifies that switching backends produces no semantic changes.

Both backends deliberately retain the existing Python token fingerprint implementation. This includes its token-number/string representation, rather than substituting Ruff tokens and invalidating saved hashes. Rust extraction is native, but token fingerprints and source-fragment compatibility still run in Python. Fingerprints retain the existing interpreter-tokenization dependency; this migration does not make graphs portable between incompatible Python tokenizer versions.

Resolution evidence now says `lexical binding and repository declaration`. The diff layer treats the old `LibCST lexical binding and repository declaration` wording as equivalent. Confidence, IDs, and the schema remain unchanged. Some unusual legacy name-resolution behavior is intentionally preserved, including LibCST's names for attributes on compound expressions. Parser-specific syntax-error wording and complexity diagnostics can differ; compatibility does not promise identical error messages or acceptance limits.

## Resource limits

Rust checks input before parsing: at most 32 MiB of UTF-8 source, 1,000,000 lexer tokens, 8,192 tokens per logical statement, 256 nested delimiters, and 128 indentation levels. After parsing, the iterative walk enforces AST depth 512 and 1,000,000 visited nodes. Ruff grows its parsing stack as needed; a dedicated 32 MiB stack also covers AST destruction. Native panics become extraction errors. These are conservative complexity controls, not process isolation or a wall-clock/memory sandbox.

LibCST keeps its independent depth-100/node budget and recursion-error handling. Failed files contribute a placeholder plus diagnostics, never partial declarations. Resolution limits are shared by both backends. Incomplete scans still emit usable partial output and return exit status 2.

## Building and validation

Install rustup and a C compiler, then:

```sh
uv sync --locked --extra all --extra test
uv run pytest
uv run ruff check src tests scripts
cargo fmt --manifest-path native/Cargo.toml --check
cargo clippy --manifest-path native/Cargo.toml --all-targets -- -D warnings
uv build
```

Maturin builds the mixed package with a PyO3 `abi3-py311` extension. Source installs compile Rust; wheels require a compatible OS/architecture but no Rust toolchain. Docker builds the extension in a build stage and copies the installed environment into the runtime stage. Ruff crates are pinned to 0.0.12, PyO3 to 0.28.2, and the toolchain to 1.96.0; the source includes `native/Cargo.lock`. Upgrade them intentionally and rerun parity tests. Ruff's component crates are [internal APIs without stability guarantees](https://docs.rs/ruff_python_parser/0.0.12/ruff_python_parser/).

## Measuring performance

```sh
uv run python scripts/benchmark_parsers.py /path/to/repository --repeat 3
```

The benchmark loads sources once, excludes disk reading and IR serialization from scan timings, and reports median extraction, module fingerprinting, resolution, graph finalization, and total time. Extraction includes the native call, conversion, declaration/reference fingerprints, and source-text compatibility work. It also reports diagnostics and exact IR equality. Backend import costs are excluded. Timing data is never written into the IR.

## Extraction cache

Successful extraction is cached by default in a shared user directory, outside the repository:

- Linux: `$XDG_CACHE_HOME/archer`, or `~/.cache/archer` when unset.
- macOS: `~/Library/Caches/archer` (an explicit `XDG_CACHE_HOME` also takes precedence).
- Windows: `%LOCALAPPDATA%/archer/Cache`.

The directory contains `facts.sqlite3`. `--cache-dir PATH` overrides the directory, and `--cache-max-mb N` sets its physical database limit in MiB (default 512). `--no-cache` disables both reads and writes, as does a zero size limit. Python APIs accept `cache=False`, `cache_dir=...`, and `cache_max_mb=...`. Configuration defaults can be set in the scanned repository:

```toml
[tool.archer]
cache = true
cache_max_mb = 512
# cache_dir = "/optional/custom/location"
```

CLI paths are relative to the current directory; relative configuration paths are relative to the scanned repository. Keep a custom cache outside the repository if Git cleanliness matters. Management commands use the user default or the explicit `--cache-dir`, without looking up repository configuration:

```sh
archer cache info
archer cache info --cache-dir /custom/cache --format json
archer cache clear
archer scan --no-cache
```

Keys include a hash of the full original source, module name, relative file path, cache format, Python version, and backend implementation/dependency identity. Formatting changes invalidate cached positions; renames and source-root changes that alter module identities cannot reuse incorrect facts. Editable adapter changes and native extension changes invalidate their entries automatically. Cache-format changes delete obsolete-format rows during maintenance.

The cache stores extraction facts and the module fingerprint, never ASTs or resolved repository graphs. Hits deserialize into fresh objects, so graph mutation cannot contaminate another scan. Both sides of a Git comparison independently construct and resolve their graphs. An unchanged caller can reuse facts while resolving to a different declaration after a dependency changes. Commit, index, and worktree snapshots can share identical entries.

Entries unused for 30 days expire. Insertion that would exceed the limit evicts least-recently-used entries toward 80% occupancy, with room reserved for the incoming entry and database overhead. Entries larger than 16 MiB (or half the configured total budget) are skipped. SQLite's page limit enforces the physical database cap, including indexes; full auto-vacuum reclaims evicted pages at commit. Shrinking the configured limit prunes existing data on the next enabled scan. Clearing leaves only the small empty database; it never removes unrelated files in the directory.

SQLite coordinates concurrent readers/writers with short lock waits. Rollback journals are kept in memory, avoiding persistent WAL or journal growth outside the disk budget. Because this is disposable storage, a crash can invalidate the database; checksums and payload validation reject bad entries, and corruption, contention, or write failures fall back to fresh extraction without marking a scan incomplete. `archer cache clear` can reset a corrupt database. Parse failures are not cached.

The parser phase benchmark explicitly disables caching, so parser timings remain comparable. Cache hits skip both extraction and module token fingerprinting; they still perform decoding, source hashing, facts validation, resolution, and graph finalization.
