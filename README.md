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

The base package includes a Rust extension using pinned Ruff crates and depends on NetworkX. Source/Git installs require the pinned Rust 1.96.0 toolchain and a C compiler; install Rust with [rustup](https://rustup.rs/). Prebuilt wheels do not require Rust. Extras are `libcst`, `render`, `semantic`, and `all`; the render extra is currently empty because D2 is an external executable. The semantic extra installs Pyright for future integration, but semantic enrichment is **not enabled in v1**. Install [D2](https://d2lang.com/tour/install/) separately for SVG, PNG, and PDF output; `--format d2` does not need it. Run `archer doctor` after installation to inspect available tools and layouts.

The `Build wheels` GitHub Actions workflow builds Linux x86_64/ARM64 (glibc 2.28+), macOS Intel/Apple Silicon, and Windows x86_64 wheels. It runs on pull requests, `v*` tag pushes, and manual dispatch. Each wheel is tested on standard CPython 3.11 and 3.14 with Rust hidden from `PATH`. Download the corresponding `wheels-PLATFORM` artifact from the workflow run, unzip it, and install its `.whl` file with `python -m pip install /path/to/archer-....whl`.

On a version-tag push, all tests must pass before the workflow attaches the wheels to a GitHub release and publishes a wheel listing through GitHub Pages. The tag must match `[project].version` exactly, prefixed by `v` (currently `v0.1.1`). Re-running a tag workflow updates that release's wheel assets. The listing retains links to wheels from all published releases, so older pinned requirements continue to work. Nothing is published to PyPI.

Before the first release, set the repository's **Settings → Pages → Build and deployment → Source** to **GitHub Actions**. If the `github-pages` environment restricts deployment refs, allow the `v*` tags. The workflow uses the built-in `GITHUB_TOKEN`; no additional token is needed. Public release assets are required for unauthenticated Docker installs. The Pages deployment replaces the repository's Pages site with the wheel index.

After a successful release and Pages deployment, a Docker project's `requirements.txt` can contain:

```text
--find-links https://schonacin.github.io/archer/wheels/
--only-binary=archer
archer==0.1.1
```

Use the actual Pages URL reported by the deployment if the repository owner, name, or domain differs. Docker's normal `python -m pip install -r requirements.txt` selects the compatible release wheel without Rust. This covers AMD64 and ARM64 Debian-based Python images (Python 3.11+); Alpine requires musllinux wheels, which this workflow does not build. For optional Python dependencies, use `archer[all]==0.1.1` instead. D2 remains a separate installation for image/PDF rendering.

```sh
archer scan --root /path/to/repo
archer render --root /path/to/repo
archer context --root /path/to/repo --focus package.module -o -
archer check --root /path/to/repo --worktree
```

D2 installation and layouts: [official installation guide](https://d2lang.com/tour/install/) and [layout documentation](https://d2lang.com/tour/layouts/). Auto layout tries TALA, ELK, then Dagre; full views or projections over 500 nodes or 250 edges prefer ELK. D2 0.9.0 bundles TALA ([release announcement](https://d2lang.com/blog/tala-is-open-source/)); older releases may need a separate plugin. `doctor` lists layouts reported by the installed D2 binary. PNG/PDF may require additional D2 browser dependencies; the container validates SVG. No normal Archer command downloads executables.

## Commands

Archer has eight commands:

| Command | Purpose |
|---|---|
| `scan` | Scan Python without importing it and write the canonical versioned JSON graph. |
| `render` | Project a graph and render D2 source, SVG, PNG, or PDF. |
| `context` | Produce bounded Markdown architecture context for a human or coding agent. |
| `diff [REV_A REV_B]` | Compare two Git snapshots as a semantic graph diff. |
| `check` | Check cycles, fan-in/out, and increased coupling against an optional baseline. |
| `cache info` / `cache clear` | Inspect or clear the shared extraction cache. |
| `doctor` | Report Archer and external-tool availability. |
| `skill` | Print the bundled Archer Agent Skill. |

At the top level, `archer -h`/`--help` displays commands and `archer --version` prints the version. Every command supports `-h`/`--help`.

### Shared scan and input flags

These flags are available on `scan`, `render`, `context`, `diff`, and `check`, except where the table says otherwise:

| Flag | Commands | Meaning |
|---|---|---|
| `--root PATH` | `scan`, `render`, `context`, `diff`, `check` | Repository to inspect. Defaults to `ARCHER_ROOT`, then the current directory. |
| `--source-root PATH` | `scan`, `render`, `context`, `diff`, `check` | Import root relative to the repository. Repeatable; otherwise Archer auto-detects `src`. |
| `--parser rust` / `--parser libcst` | `scan`, `render`, `context`, `diff`, `check` | Extraction backend. Defaults to Rust; LibCST requires `archer[libcst]` (also included in `all`). |
| `--no-cache` | `scan`, `render`, `context`, `diff`, `check` | Disable extraction cache reads and writes. |
| `--cache-dir PATH` | `scan`, `render`, `context`, `diff`, `check` | Override the user cache directory. |
| `--cache-max-mb N` | `scan`, `render`, `context`, `diff`, `check` | Cache database limit in MiB; default 512, zero disables caching. |
| `--exclude GLOB` | `scan`, `render`, `context`, `diff`, `check` | Repository-relative source exclusion. Repeatable; quote shell globs. |
| `-o PATH`, `--output PATH` | `scan`, `render`, `context`, `diff`, `check` | Explicit destination. `-` streams to stdout. Scan, render, context, and diff otherwise use descriptive files under `./archer/`; check defaults to stdout. |
| `--graph FILE` | `scan`, `render`, `context`, `check` | Read an existing Archer JSON graph instead of scanning. Not available on `diff`. |

`--source-root` and `--exclude` affect scanning. Repository defaults may also be stored as `source_roots` and `exclude` under `[tool.archer]` in `pyproject.toml`; explicit CLI values replace the corresponding configured list.

An optional `archer.yaml` supplies defaults for every command flag. Use long flag names without `--` (underscores are also accepted), YAML booleans for switches, and lists for repeatable flags. Top-level flags apply to commands that support them; command sections override those shared defaults:

```yaml
source-root: [src]
exclude: [scripts/*]
parser: rust
no-cache: false
render:
  level: modules
  format: svg
  svg-optimization: fast
  color-arrows: true
  exclude-arrows-to: [acme.models]
context:
  max-chars: 16000
check:
  fan-threshold: 20
cache:
  format: json
  info:
    cache-dir: .cache/archer
doctor:
  format: json
```

Archer looks for `archer.yaml` in `--root`, otherwise `ARCHER_ROOT`, otherwise the current directory. It works normally when the file is absent or empty. A configured `root` is resolved relative to that YAML file; it does not trigger another config lookup. Other configured file paths are also relative to the YAML file, while explicit CLI paths remain relative to the working directory. `output: "-"` selects stdout.

Precedence is explicit CLI flags, command YAML defaults, shared YAML defaults, then existing `[tool.archer]` scan settings and built-in defaults. Explicit repeatable flags replace configured lists. Snapshot switches such as `staged: true` and `worktree: true` are mutually exclusive; an explicit CLI snapshot flag overrides configured snapshot switches. Help/version actions and positional revision arguments remain CLI-only.

An optional `.archerignore` in the repository root excludes Python source files using Git-style patterns, including comments, directory patterns, `**`, and `!` negation:

```gitignore
# Skip generated code and test fixtures
generated/
tests/fixtures/**
*_generated.py
!handwritten_generated.py
```

Ignore rules supplement `exclude` and `--exclude`; negation cannot restore files excluded by those options or Archer's built-in exclusions. The current repository's `.archerignore` applies consistently to filesystem scans and both sides of Git comparisons, including commits and the index. Neither configuration file needs to be committed.


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
| `--initializers auto\|all` | Hide unused structural initializer leaves by default; `all` restores every leaf. |
| `--exclude-arrows MODULE` | Hide arrows both to and from the module and its descendants. Repeatable. |
| `--exclude-arrows-to MODULE` | Hide arrows entering the module and its descendants. Repeatable. |
| `--exclude-arrows-from MODULE` | Hide arrows leaving the module and its descendants. Repeatable. |
| `--color-arrows true\|false` | Color arrows by their source subsystem (default `true`; a bare flag also enables it). Git-change diagrams retain change colors. |
| `--svg-optimization raw\|medium\|fast` | SVG post-processing level; default `fast`. |
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

Scanning isolates each file: failed extraction retains a module placeholder and a diagnostic, without partial declarations or relationships from that file. Rust uses an iterative AST walk with depth 512 and 1,000,000-node limits; pre-parse limits also bound source size, tokens, and nesting. LibCST retains its depth-100 and 1,000,000-node limits. Alias and inherited-member resolution use iterative searches with a 10,000-step budget per reference; alias names are limited to 4,096 characters. Exhausted searches retain an unresolved reference and a resolution diagnostic. Limits can mark unusually complex valid code incomplete. Diagnostics identify the file and stage; filesystem and Git snapshot scans use the same selected backend. See [parser architecture and compatibility](docs/PARSERS.md) for the contract, limits, and benchmarks.


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
| `medium` | Vector clips for opaque cutouts, tightly bounded masks for fading | `-medium.svg` |
| `fast` (default) | Replace supported masks with vector clips and faded copies of connector geometry | `.svg` |

Fast mode preserves embedded fonts, connector geometry, dash phase, colors, and draw order. It approximates compositing at antialiased clip boundaries; use medium when fidelity matters most. Unsupported SVG constructs retain their masks. Fast removes the expensive mask operations for supported D2 diagrams, but actual viewer speed should be compared on your device.

```bash
archer render --level modules --svg-optimization raw
archer render --level modules --svg-optimization medium
archer render --level modules --svg-optimization fast
```

`--no-optimize-svg` remains an alias for `--svg-optimization raw`. PNG/PDF and D2-source output are unchanged. SVG engines can differ slightly in clip-versus-mask antialiasing, especially when a large diagram is reduced to a tiny overview; no geometry or text precision is reduced. Viewer-specific pan/zoom gains depend on the SVG engine.

Arrows use the source subsystem's border color by default. Disable this with `--color-arrows false` (producing `-plain-arrows` filenames), or enable it explicitly with `--color-arrows true`. The option is ignored for Git diffs, which preserve their change colors.

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

Diagrams nest modules under their packages (`acme.models.article` inside `acme` → `models`). Detailed views place symbols inside their modules and methods inside their declaring classes, including nested classes. Classes without visible members remain ordinary nodes. Ownership is shown by nesting; calls and inheritance remain arrows. Class-level arrows attach to the class boundary. When a class has relationships with its own descendants, a bold text header inside the class provides their endpoint, preserving compatibility with every layout engine. Labels are relative to their container.

With `--initializers auto` (the default), diagrams omit empty or docstring/import-only `__init__.py` leaves when they have no incoming relationships and only resolved imports into their own package. Their package containers remain visible; their import arrows are omitted, never redirected or rewired. Namespace packages likewise need no redundant leaf unless referenced. Initializers with declarations, assignments (including `__all__`), startup code, external imports, uncertain resolution, or parse errors remain visible. This is a diagram simplification, not a claim that importing a module has no side effects. Use `--initializers all` to inspect every initializer and its relationships. Full, changes, Git diff, and explicitly focused views always retain them; older saved graphs without initializer classification also retain source-backed leaves. Scans, dependency analysis, and context output keep the complete graph. Focused views restore ancestor containers for orientation without adding dependencies or expanding the selected neighborhood.

Entity shapes stay consistent across views: packages and modules use square corners, classes use rounded corners with a stronger border, and functions/methods use softer rounded corners with a thin border. Methods and functions share a shape because nesting conveys their ownership. Compact kind labels distinguish entities even without surrounding context; a small legend explains shapes, nesting, and colors. Initializer leaves remain rectangular.

Ordinary diagrams color subsystems by the first two components of a module name, with nested items sharing the same color. Focused views preserve colors from the original scan. Git diff graphs use green for added, red for removed, and amber for direct modifications; subsystem colors are disabled in these graphs. Existing containers with changed descendants use a neutral fill and dark amber border, and summarize descendant changes, for example `3 functions modified`. Their `module body / imports` (or `__init__.py body / imports`) leaf highlights changes owned by the file itself. Classes and functions likewise stay neutral when only nested declarations changed, with counts identifying the changed descendants. Class headers, decorators, signatures, and directly owned statements still count as direct changes. Collapsed scopes retain this border when their changed symbols are hidden. Body/import leaves keep a normal border when unchanged. Added/removed containers retain their whole-container color.

Counts are computed before projection, so module/type views still explain changes to hidden symbols. Radius controls dependency expansion, not symbol detail or whether unchanged symbols appear. The original whole-source change status remains in the graph for analysis; diagram attribution uses separate scope fingerprints excluding child declarations. If an older graph or unsupported attribution syntax lacks those fingerprints, changed elements remain amber and are labeled `scope unknown`. Rescanning both snapshots enables precise attribution.

A focused module neighborhood can be rendered at any level:

```sh
archer render --root /path/to/repository --level modules \
  --focus acme.processing.pipeline --radius 1 -o pipeline.svg
```

This includes the selected module and its immediate incoming and outgoing module dependencies. The same options work for any module or symbol.

## Discovery and semantics

Python 3.11+ is required. Rust extraction using Ruff is the default. Select the Python/LibCST implementation with `--parser libcst` or `parser = "libcst"` in `[tool.archer]`. An unavailable backend is an explicit error, with no automatic fallback. Sources auto-detect `src/`; otherwise the repository is the import root. Hidden directories, virtualenvs, build outputs, and symlinks are skipped. Normal scans include tests. Narrow scans with repeatable `--exclude 'tests/*'` and `--source-root src`. Module name collisions fail explicitly. Configuration can live in the scanned repository:

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

The image installs this package and all extras from `uv.lock` using `uv sync --locked --extra all` and a pinned, checksum-verified D2 release. The test target includes pytest and Ruff. Repository mounts are read-only; Compose sets `ARCHER_ROOT=/repo` and works from the writable `/output` mount. Default files therefore go to `artifacts/archer/` on the host; explicit `/output/FILE` paths go directly to `artifacts/`. For local development, install Rust with rustup (the repository pins its toolchain), then run `uv sync --extra all --extra test`, `uv run pytest`, `uv run ruff check src tests`.

The [validation report](docs/VALIDATION.md) records automated coverage, packaging checks, Docker verification, and renderer validation.
