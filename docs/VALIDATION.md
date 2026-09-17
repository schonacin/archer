# Archer 0.1 validation

## Automated verification

- 152 tests pass locally and in the Docker test image, including actual D2 SVG rendering.
- Ruff lint and formatting checks pass.
- The uv lockfile is current.
- Wheel and source distributions build successfully.
- `uv tool install --force './[all]'` installs the local package and exposes `archer` version `0.1`.
- The built wheel contains the bundled Agent Skill, and the skill passes its structural validator.

The tests cover both parser backends, lexical shadowing, conditional re-exports, relative imports, namespace packages, repeated definitions, constructor-injected dependencies, inherited methods, uncertain calls, stable IDs, token-level changes, source ranges, parse diagnostics, graph round trips, Git snapshot modes, deleted nodes, neighborhoods, strongly connected component growth, fan and coupling checks, CLI failures, renderer fallbacks, nested modules, diagram colors, arrow exclusions, and SVG post-processing.

## Packaging and runtime boundaries

The base package supports scan, context, check, and D2-source generation without D2 on `PATH`. Image and PDF rendering exits with actionable installation guidance when D2 is unavailable. Docker images install the locked package and checksum-verified D2 binaries for AMD64 or ARM64. Repository mounts are read-only and generated output is written through the separate `/output` mount.

## Diagram validation

Synthetic package graphs exercise module, type, symbol, full, and Git-change projections. Tests render nested package structures through ELK, Dagre, and TALA when D2 is available. They verify stable subsystem colors, preserved Git-change colors, source-colored arrows, focused ancestor containers, aggregated module dependencies, and incoming, outgoing, and bidirectional arrow exclusions for module subtrees.

## SVG post-processing

The SVG suite uses compact synthetic fixtures over colored and gradient backgrounds. It covers opaque, overlapping, and translucent label cutouts; curve bounds; dash and marker preservation; unsupported-feature fallback; embedded font and text preservation; idempotence; and all three optimization modes.

- `raw` returns the original D2 bytes.
- `medium` replaces supported opaque mask cutouts with vector clips and bounds translucent masks to relevant connectors.
- `fast` replaces supported masks with clipped connector copies and opacity, accepting small antialiasing differences at clip boundaries.

Pixel comparisons are performed with resvg. These checks establish visual equivalence within the tested fixtures, not universal pan and zoom performance across SVG viewers. The generic comparison utility can benchmark a directory of raw D2 SVG files:

```sh
uv run --extra test python scripts/validate_svg.py path/to/raw-svg \
  --output artifacts/svg-validation --optimization medium
```

Use `--optimization raw`, `medium`, or `fast` to compare modes. The generated artifacts are gitignored.

## Known limits

Resolution remains static and conservative. Dynamic containers, arbitrary object flows, reflection, wildcard exports, and external library implementations can remain unresolved. The semantic extra installs Pyright for future adapters; release 0.1 does not claim to run semantic enrichment. Renames appear as removed and added symbols. Full graphs can be large, so focused and module views are the practical starting points. PNG and PDF exports depend on the browser environment available to D2.

## Parser migration validation (2026-09-16)

- Both backends run the scanner regression suite. Differential tests compare complete IR for semantic fixtures and Archer's source tree.
- A graph captured from the pre-migration implementation verifies saved-graph compatibility, including fingerprints and unresolved IDs.
- Deep native inputs are exercised in subprocesses, and missing optional backends are explicit errors.
- Source distribution and Linux ARM64 ABI3 wheel builds pass. A clean environment with only the base wheel and NetworkX scans with Rust, preserves the legacy fixture, and includes the bundled skill without LibCST installed.
- Ruff, Rustfmt, and Clippy checks pass.
- The Docker production image builds and scans with the Rust backend without a Rust toolchain in the runtime stage.

On this Linux ARM64 workspace with Python 3.12.3, a three-run median over 26 repository Python files produced identical IR and no diagnostics:

| Phase | Rust | LibCST |
| --- | ---: | ---: |
| Extraction (including compatibility work) | 0.105 s | 2.487 s |
| Module fingerprints | 0.027 s | 0.028 s |
| Resolution | 0.017 s | 0.018 s |
| Finalization | 0.002 s | 0.002 s |
| Total scan | 0.154 s | 2.539 s |

This is about 16.5× on this small repository, not a general speed guarantee. Disk reads, initial backend imports, serialization, and D2 rendering are excluded. Use `scripts/benchmark_parsers.py` on larger target repositories before extrapolating.

## Extraction cache validation

The cache suite covers warm/cold IR equality for both backends, full-source and context invalidation, object isolation, physical disk-size limits, LRU eviction, expiry, format cleanup, oversized entries, corrupt entries/databases, lock contention, concurrent writers, disabled caching, and CLI controls. Git tests prove that unchanged callers reuse extraction facts while resolving changed dependencies, and compare cached/uncached graphs and diffs for commits, index, worktree, renames, and deletions.

A local run over 29 Python files measured 0.231 seconds cold, 0.050 seconds warm (median of three), and 0.198 seconds without caching, with identical IR. This measures scanning after reading sources into memory; results depend on repository size and storage. Parser-only benchmarks now explicitly disable caching.
