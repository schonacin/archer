# Archer 0.1 validation

## Automated verification

- 68 tests pass locally and through `docker compose run --rm test`, including actual D2 SVG rendering.
- Ruff lint and formatting checks pass.
- The uv lockfile is current.
- Wheel and source distributions build successfully.
- `uv tool install --force './[all]'` installs the local package and exposes `archer` version `0.1`.
- The built wheel contains the bundled Agent Skill, and the skill passes its structural validator.

The tests cover LibCST entities, lexical shadowing, conditional re-exports, relative imports, namespace packages, repeated definitions, constructor-injected dependencies, inherited methods, uncertain calls, stable IDs, token-level changes, source ranges, parse diagnostics, graph round trips, Git snapshot modes, deleted nodes, neighborhoods, strongly connected component growth, fan and coupling checks, CLI failures, renderer fallbacks, nested modules, diagram colors, arrow exclusions, and SVG post-processing.

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
