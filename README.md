# Archer

Archer 0.1 builds a static architecture graph of a Python repository, renders D2 diagrams, produces bounded agent context, compares Git snapshots, and checks dependency structure. It never imports or executes the scanned project.

> **Development disclaimer:** Archer was coded solely by AI, with human planning and steering.

## Installation

Archer requires Python 3.11 or newer. Once the repository is public, install the CLI and all optional Python dependencies directly from GitHub with uv:

```sh
uv tool install 'archer[all] @ git+https://github.com/schonacin/archer.git'
```

Other supported approaches are:

```sh
# pipx keeps the command in an isolated environment
pipx install 'archer[all] @ git+https://github.com/schonacin/archer.git'

# pip installs into the active Python environment
python -m pip install 'archer[all] @ git+https://github.com/schonacin/archer.git'

# install a local clone with uv
git clone https://github.com/schonacin/archer.git
cd archer
uv tool install '.[all]'
```

Append `@TAG_OR_COMMIT` to the Git URL to pin an installation. For example, a tagged release can use `git+https://github.com/schonacin/archer.git@v0.1`. Upgrade a uv installation by repeating the command with `--force`.

The base package only needs LibCST and NetworkX. Extras are `render`, `semantic`, and `all`; the render extra is currently empty because D2 is an external executable. The semantic extra installs Pyright for future integration, but semantic enrichment is **not enabled in v1**. Install [D2](https://d2lang.com/tour/install/) separately for SVG, PNG, and PDF output; `--format d2` does not need it. Run `archer doctor` after installation to inspect available tools and layouts.

```sh
archer scan --root /path/to/repo
archer render --root /path/to/repo
archer context --root /path/to/repo --focus package.module -o -
archer check --root /path/to/repo --worktree
```

D2 installation and layouts: [official installation guide](https://d2lang.com/tour/install/) and [layout documentation](https://d2lang.com/tour/layouts/). Auto layout tries TALA, ELK, then Dagre; full views or projections over 500 nodes or 250 edges prefer ELK. D2 0.9.0 bundles TALA ([release announcement](https://d2lang.com/blog/tala-is-open-source/)); older releases may need a separate plugin. `doctor` lists layouts reported by the installed D2 binary. PNG/PDF may require additional D2 browser dependencies; the container validates SVG. No normal Archer command downloads executables.

## Commands

Archer has seven commands:

| Command | Purpose |
|---|---|
| `scan` | Scan Python without importing it and write the canonical versioned JSON graph. |
| `render` | Project a graph and render D2 source, SVG, PNG, or PDF. |
| `context` | Produce bounded Markdown architecture context for a human or coding agent. |
| `diff [REV_A REV_B]` | Compare two Git snapshots as a semantic graph diff. |
| `check` | Check cycles, fan-in/out, and increased coupling against an optional baseline. |
| `doctor` | Report Archer and external-tool availability. |
| `skill` | Print the bundled Archer Agent Skill. |

At the top level, `archer -h`/`--help` displays commands and `archer --version` prints the version. Every command supports `-h`/`--help`.

### Shared scan and input flags

These flags are available on `scan`, `render`, `context`, `diff`, and `check`, except where the table says otherwise:

| Flag | Commands | Meaning |
|---|---|---|
| `--root PATH` | `scan`, `render`, `context`, `diff`, `check` | Repository to inspect. Defaults to `ARCHER_ROOT`, then the current directory. |
| `--source-root PATH` | `scan`, `render`, `context`, `diff`, `check` | Import root relative to the repository. Repeatable; otherwise Archer auto-detects `src`. |
| `--exclude GLOB` | `scan`, `render`, `context`, `diff`, `check` | Repository-relative source exclusion. Repeatable; quote shell globs. |
| `-o PATH`, `--output PATH` | `scan`, `render`, `context`, `diff`, `check` | Explicit destination. `-` streams to stdout. Scan, render, context, and diff otherwise use descriptive files under `./archer/`; check defaults to stdout. |
| `--graph FILE` | `scan`, `render`, `context`, `check` | Read an existing Archer JSON graph instead of scanning. Not available on `diff`. |

`--source-root` and `--exclude` affect scanning. Repository defaults may also be stored as `source_roots` and `exclude` under `[tool.archer]` in `pyproject.toml`; explicit CLI values replace the corresponding configured list.

### Git snapshot flags

`render`, `context`, `diff`, and `check` accept one of these mutually exclusive modes:

| Flag | Comparison |
|---|---|
| `--staged` | `HEAD` → Git index |
| `--worktree` | `HEAD` → working tree |
| `--unstaged` | Git index → working tree |

Without a mode, `diff` and `--changes` use `HEAD` → working tree. Worktree snapshots include untracked, nonignored Python files and omit deleted files. Git objects are read without checkout or index mutation. `--graph` cannot be combined with a snapshot mode.

### `scan`

`scan` supports the shared flags plus `--graph FILE`. It emits the canonical JSON IR, including diagnostics and unresolved references. Passing `--graph` validates and re-emits a saved graph, which is useful for normalized naming or piping.

### `render`

`render` supports all shared flags, Git snapshot flags, and the following:

| Flag | Meaning |
|---|---|
| `--level modules\|types\|symbols\|full\|changes` | Projection detail; default `modules`. `changes` displays a change neighborhood at full detail. |
| `--changes` | Render a Git-change neighborhood while retaining the selected `--level`. |
| `--radius N` | Module dependency distance for `--focus` or change neighborhoods; default `2`. |
| `--direction both\|incoming\|outgoing` | Direction used to build that neighborhood; default `both`. |
| `--focus MODULE_OR_SYMBOL` | Restrict the graph to a module-centered neighborhood around an exact ID or qualified name. |
| `--exclude-arrows MODULE` | Hide arrows both to and from the module and its descendants. Repeatable. |
| `--exclude-arrows-to MODULE` | Hide arrows entering the module and its descendants. Repeatable. |
| `--exclude-arrows-from MODULE` | Hide arrows leaving the module and its descendants. Repeatable. |
| `--color-arrows` | Color arrows by their source subsystem. Git-change diagrams retain change colors. |
| `--svg-optimization raw\|medium\|fast` | SVG post-processing level; default `medium`. |
| `--no-optimize-svg` | Alias for `--svg-optimization raw`; mutually exclusive with that flag. |
| `--format d2\|svg\|png\|pdf` | Output format; default `svg`. |
| `--layout auto\|tala\|elk\|dagre` | D2 layout engine; default `auto`. |
| `--external` | Include external and unresolved nodes in projected views. Full views already include them. |

Module diagrams hide containment arrows and combine parallel relationship labels. Arrow exclusions retain nodes, apply to module subtrees at every detail level, and may be repeated for several modules. D2 source output works without D2; image and PDF output require the external executable. An explicit output extension must match `--format`.

### `context`

`context` supports all shared flags, Git snapshot flags, `--level`, `--changes`, `--radius`, `--direction`, and `--focus` with the same meanings as `render`. It also accepts `--max-chars N`, whose default of `12000` is a hard output budget. The result summarizes hierarchy, locations, dependencies, changed relationships, cycles, and coupling.

### `diff`

`diff` accepts zero or two positional revisions plus the shared flags other than `--graph`, and the Git snapshot flags. Use `archer diff REV_A REV_B` for two commits or refs. With no revisions or mode it compares `HEAD` with the working tree. Deleted entities remain in the result; unmerged indexes are rejected.

### `check`

`check` supports the shared flags and Git snapshot flags, plus:

| Flag | Meaning |
|---|---|
| `--baseline REV` | Compare the current working tree with a Git revision. Cannot be combined with a snapshot mode. |
| `--fan-threshold N` | Report module fan-in or fan-out above this nonnegative threshold; default `15`. |
| `--coupling-threshold N` | Allowed module-coupling increase from the baseline; default `0`. |
| `--format text\|json` | Finding output format; default `text`. |

Checks on a current graph report cycles and high fan-in/out. Baseline checks additionally detect new or growing strongly connected components and increased module coupling. Checks require an ordinary snapshot graph rather than a diff graph. Rules are functions in `archer.checks.RULES`.

### `doctor` and `skill`

`doctor` accepts `--format text|json` and reports Git, D2, available D2 layouts, TALA, ty, and Pyright. `skill` has no command-specific flags and prints the Agent Skill included in the installed wheel.

Exit status is `0` for success, `1` when `check` finds architecture findings, and `2` for an operational error or incomplete scan. An incomplete scan still emits its partial graph or output.

Scanning isolates each file: failed extraction retains a module placeholder and a diagnostic, without partial declarations or relationships from that file. Syntax trees exceeding depth 100 or 1,000,000 nodes are skipped before LibCST metadata processing. Python recursion errors during parsing, metadata, or extraction are also reported. Alias and inherited-member resolution use iterative searches with a 10,000-step budget per reference; alias names are limited to 4,096 characters. Exhausted searches retain an unresolved reference and a resolution diagnostic. These conservative limits can mark unusually complex valid code incomplete. Diagnostics identify the file and processing stage; filesystem and Git snapshot scans use the same limits. The syntax-tree check runs after parsing and is not a process-level memory or timeout limit.


## Output files

Artifact-producing commands write under `archer/` in the **current working directory**, even when `--root` points elsewhere. `-o PATH` overrides the destination; `-o -` streams text or rendered image bytes to stdout. Parent directories are created automatically. Check/doctor/skill diagnostics continue to use stdout by default. `ARCHER_ROOT` can set the default scan root; an explicit `--root` takes precedence.

| Command | Default file |
|---|---|
| `archer scan` | `archer/graph.json` |
| `archer diff --staged` | `archer/graph-diff-staged.json` |
| `archer diff --worktree` | `archer/graph-diff-worktree.json` |
| `archer diff --unstaged` | `archer/graph-diff-unstaged.json` |
| `archer diff REV_A REV_B` | `archer/graph-diff-SHA_A-to-SHA_B.json` (eight-character revisions) |
| `archer render` | `archer/architecture-modules.svg` |
| `archer render --level types --format d2` | `archer/architecture-types.d2` |
| `archer render --changes --level symbols --radius 1` | `archer/architecture-changes-worktree-symbols-r1.svg` |
| `archer context` | `archer/context-modules.md` |

Focused filenames include `focus-NAME-rN`; nondefault traversal direction, external-node inclusion, and colored arrows also get suffixes. Saved diff graphs use their recorded snapshot metadata for naming. Repeating the same command overwrites its corresponding file.

## SVG viewing performance

SVG exports are post-processed automatically after D2 finishes, at every level. D2 0.9.0 has no switch to remove its global label masks: its [SVG renderer](https://github.com/d2lang/d2/blob/v0.9.0/d2renderers/d2svg/d2svg.go) attaches the shared mask to each connector.

Archer replaces opaque rectangular cutouts with small vector clips, culls cutouts that cannot touch each connector, and bounds remaining translucent masks to their connectors. This avoids repeatedly evaluating every label on every edge. It preserves path geometry, arrowheads, dash patterns, fonts, text, colors, draw order, and transparent/colored backgrounds. It does not rasterize the diagram or paint background rectangles over other content. Unknown masks, path commands, or transforms are left untouched.

Choose `--svg-optimization raw|medium|fast` at any diagram level:

| Mode | Processing | Default filename suffix |
| --- | --- | --- |
| `raw` | Original D2 bytes, no post-processing | `-raw.svg` |
| `medium` (default) | Vector clips for opaque cutouts, tightly bounded masks for fading | `.svg` |
| `fast` | Replace supported masks with vector clips and faded copies of connector geometry | `-fast.svg` |

Fast mode preserves embedded fonts, connector geometry, dash phase, colors, and draw order. It approximates compositing at antialiased clip boundaries; use medium when fidelity matters most. Unsupported SVG constructs retain their masks. Fast removes the expensive mask operations for supported D2 diagrams, but actual viewer speed should be compared on your device.

```bash
archer render --level modules --svg-optimization raw
archer render --level modules --svg-optimization medium
archer render --level modules --svg-optimization fast
```

`--no-optimize-svg` remains an alias for `--svg-optimization raw`. PNG/PDF and D2-source output are unchanged. SVG engines can differ slightly in clip-versus-mask antialiasing, especially when a large diagram is reduced to a tiny overview; no geometry or text precision is reduced. Viewer-specific pan/zoom gains depend on the SVG engine.

Opt into source-colored arrows with `--color-arrows`. They use the source subsystem's border color and produce `-colored-arrows` filenames. The flag is ignored for Git diffs, which preserve their existing change colors.

Suppress visually noisy dependencies without removing their nodes:

```bash
archer render --exclude-arrows-to acme.models
archer render --exclude-arrows-from acme.infrastructure
archer render --exclude-arrows acme.models
```

Each option is repeatable and applies to the named module plus its descendants. Default filenames record every arrow exclusion.

## Diagram levels and grouping

| Level | Included entities |
|---|---|
| `modules` | Packages and modules; symbol relationships are aggregated to modules. |
| `types` | Packages, modules, and classes. |
| `symbols` | Packages, modules, classes, functions, nested functions, and methods. |
| `full` | All extracted entities, including external and unresolved targets. |
| `changes` | A Git-change neighborhood displayed at full detail; use `--changes --level modules` for a module-level change diagram. |

Diagrams nest modules under their packages (`acme.models.article` inside `acme` → `models`). Detailed views also place symbols inside their modules. Labels are relative to their container. Package initializers appear as `__init__.py` leaves so their own import relationships remain explicit. Focused views restore ancestor containers for orientation without adding dependencies or expanding the selected neighborhood.

Ordinary diagrams color subsystems by the first two components of a module name, with nested items sharing the same color. Focused views preserve colors from the original scan. Git diff graphs keep green for added, red for removed, amber for modified, and neutral colors for unchanged entities; subsystem colors are disabled in these graphs.

A focused module neighborhood can be rendered at any level:

```sh
archer render --root /path/to/repository --level modules \
  --focus acme.processing.pipeline --radius 1 -o pipeline.svg
```

This includes the selected module and its immediate incoming and outgoing module dependencies. The same options work for any module or symbol.

## Discovery and semantics

Python 3.11+ is required. LibCST is the primary parser and lexical metadata engine. Sources auto-detect `src/`; otherwise the repository is the import root. Hidden directories, virtualenvs, build outputs, and symlinks are skipped. Normal scans include tests. Narrow scans with repeatable `--exclude 'tests/*'` and `--source-root src`. Module name collisions fail explicitly. Configuration can live in the scanned repository:

```toml
[tool.archer]
source_roots = ["src"]
exclude = ["tests/*", "scripts/*"]
```

The IR has `schema_version: "1.0"`, nodes, edges, and metadata. IDs use qualified names, without line numbers; repeated declarations get occurrence suffixes. Containment, imports, calls, and inheritance are represented. Relationship edges aggregate repeated call sites in `metadata.sites`. Namespace packages are included. External/unresolved targets are explicit nodes. Resolution includes status, confidence, provider, and evidence. Confidence values are conservative labels, not calibrated probabilities.

Diffs ignore positions, formatting, and comments, preserve deleted entities, and retain old data on modified entities. Token fingerprints detect implementation changes. Renames appear as additions/removals; repeated same-name declarations are occurrence-based. The scanner resolves lexical bindings, import aliases/re-exports, class members, inherited methods, and simple annotated/constructed receiver attributes. Calls through dynamic containers, arbitrary instance flows, wildcard imports, metaprogramming, and external library internals can remain unresolved. Multiple bindings are ambiguous. No whole-program type inference is claimed.

Module dependency checks ignore external targets and containment. SCCs summarize cycles without enumerating exponentially many cycle paths. Graph algorithms use NetworkX; scans and Git snapshots are in memory.

## Docker and validation

```sh
docker compose build
docker compose run --rm test
docker compose run --rm archer doctor
docker compose run --rm archer scan --root /repo -o /output/graph.json
# Set ARCHER_REPO to the absolute path of the repository to inspect.
ARCHER_REPO=/absolute/path/to/project docker compose run --rm archer render --root /repo -o /output/architecture.svg
```

The image installs this package and all extras from `uv.lock` using `uv sync --locked --extra all` and a pinned, checksum-verified D2 release. The test target includes pytest and Ruff. Repository mounts are read-only; Compose sets `ARCHER_ROOT=/repo` and works from the writable `/output` mount. Default files therefore go to `artifacts/archer/` on the host; explicit `/output/FILE` paths go directly to `artifacts/`. For local development: `uv sync --extra all --extra test`, `uv run pytest`, `uv run ruff check src tests`.

The [validation report](docs/VALIDATION.md) records automated coverage, packaging checks, Docker verification, and renderer validation.
