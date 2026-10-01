# Archer 0.1 validation

## Automated verification

- 260 tests pass in the complete local suite, including actual D2 SVG rendering and browser tests; 6 browser cases are intentionally skipped because the 50-cycle, CDP touch, and performance-reference checks run once in Chromium rather than redundantly in all three engines.
- Ruff lint and formatting checks pass.
- The uv lockfile is current.
- Wheel and source distributions build successfully.
- `uv tool install --force '.[all]'` installs the local package and exposes `archer` version `0.1.1`.
- The built wheel contains the bundled Agent Skill, and the skill passes its structural validator.

The tests cover both parser backends, lexical shadowing, conditional re-exports, relative imports, namespace packages, repeated definitions, constructor-injected dependencies, inherited methods, uncertain calls, stable IDs, token-level changes, source ranges, parse diagnostics, graph round trips, Git snapshot modes, deleted nodes, neighborhoods, strongly connected component growth, fan and coupling checks, CLI failures, renderer fallbacks, nested modules, diagram colors, arrow exclusions, and SVG post-processing.

## Packaging and runtime boundaries

The base package supports scan, context, check, and D2-source generation without D2 on `PATH`. Image, PDF, and HTML rendering exits with actionable installation guidance when D2 is unavailable. The HTML runtime and stylesheet are wheel resources and the artifact has no remote imports, fetches, workers, telemetry, or storage dependency. Docker images install the locked package and checksum-verified D2 binaries for AMD64 or ARM64. Repository mounts are read-only and generated output is written through the separate `/output` mount.

## Semantic explorer

The HTML exporter prepares one root scene, one type scene per visible source module, and one member scene per included class. It validates reciprocal scene links, displayed edge endpoints, positive geometry, D2's encoded shape identities, active SVG content, external resource references, and per-scene resource namespaces before atomically replacing the destination. Scenes render bottom-up so child view-box aspect ratios determine parent card buckets.

Automated checks cover inventory and ownership, nested/local classes and functions, module-level function metadata, empty scenes, cross-boundary and unresolved context, long Unicode names, deterministic dimensions, all three layouts and SVG optimization modes, both sizing modes, manifest failures, incompatible CLI options, output naming, inert JSON scene packaging, and direct `file://` operation. Browser checks cover unselected click-free wheel descent through all three levels, automatic zoom-out ascent, reverse transition, resize commit, keyboard/focus/reduced-motion navigation, mount bounds, 50 cycles, and a Chromium two-contact pointer sequence. Physical-device pinch behavior remains explicitly untested; desktop protocol emulation is not evidence of touch hardware quality.

Browser tests use a separate dependency group and do not install browser binaries during a normal Archer installation:

```sh
uv sync --group browser-test
uv run --group browser-test playwright install chromium firefox webkit
uv run --group browser-test pytest tests/browser
```

The common browser checks pass in Chromium 153, Firefox 155, and WebKit 26.6 through Playwright 1.63. Click-free descent, zoom-out ascent, reversal, resize, keyboard/reduced-motion navigation, and offline request checks run in every engine. Chromium additionally runs the 50-cycle mount/heap-stability, multi-contact protocol, and performance-reference checks; post-GC growth over the second 25-cycle interval must remain below 2 MiB.

On this Linux ARM64 workspace with Python 3.12.3, D2 0.9.0, and headless Chromium, the generated reference fixture contains 200 embedded scenes, 199 expandable cards and 198 displayed root connectors. Its 3.03 MiB artifact built in 9.82 seconds with `auto`; TALA reached its optimizer resource limit on the root and the recorded fallback selected ELK. It measured 30.7 ms from runtime start to usable root, 1.3 ms target-scene preparation, 16.7 ms p95 transition frame interval, zero observed long tasks over 50 ms, one idle/two transitional mounted SVGs, and one `file://` request. The repeatable test uses broad guards of 60 seconds build time, 20 MiB output, 2 seconds startup, 100 ms preparation, and 20 ms p95 frames.

Entry frames at approximately 0/20/50/80/100 percent and reverse frames after child panning were captured from the real Chromium runtime and reviewed. The child remained uniformly scaled inside the selected-card portal, outgoing connectors disappeared before incoming connectors appeared, and no detached arrow, double-route frame, text distortion, flash, or visible commit jump was observed. Camera round-trip tests provide the exact continuity check for negative origins and letterboxing; visual inspection is not used as a substitute for those transform assertions.

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

Resolution remains static and conservative. Dynamic containers, arbitrary object flows, reflection, wildcard exports, and external library implementations can remain unresolved. The semantic extra installs Pyright for future adapters; release 0.1 does not claim to run semantic enrichment. Renames appear as removed and added symbols. Full graphs can be large, so focused and module views are the practical starting points. PNG and PDF exports depend on the browser environment available to D2. Semantic HTML is snapshot-only and does not provide search, source navigation, module-free-function drill-down, or runtime layout.

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

## Ruff parser upgrade validation (2026-10-01)

- Ruff component crates upgraded from 0.0.12 to 0.0.15 with Rust 1.96.0 unchanged.
- All 195 tests pass on Linux x86_64 with Python 3.12.14, including Rust/LibCST parity, saved-graph compatibility, cache coverage, and actual D2 rendering.
- Ruff lint, Rustfmt, and Clippy with warnings denied pass; `uv sync --locked --extra all --extra test` succeeds.
- `uv build` produces the source distribution and a CPython 3.11+ ABI3 Linux x86_64 wheel. The wheel installs in a clean base-only environment and passes `scripts/smoke_wheel.py` with Rust hidden from `PATH` and LibCST absent.
- Other platform wheels and Python versions remain covered by the GitHub Actions wheel workflow; they were not exercised locally for this upgrade.
