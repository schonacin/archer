---
name: archer
description: Inspect Python repository architecture with Archer, or validate architectural changes when the user requests architecture review or cleanliness.
---

# Archer workflow

Use Archer to inspect a Python repository without importing or executing it. Start with `archer context --root REPO -o -` for a bounded overview. Use `scan` for machine-readable details, `render` for diagrams, `diff` for Git comparisons, and `check` when the user asks for architecture validation.

Resolution status and evidence matter: inferred, ambiguous, and unresolved calls are not proven runtime dependencies. Dynamic dispatch, monkeypatching, reflection, arbitrary instance flows, and external internals may remain unresolved. Check cited source locations before making strong claims. Treat cycles, fan counts, and coupling changes as architectural signals rather than proof of a defect.

## Installation and dependencies

Archer requires Python 3.11 or newer. From a public GitHub repository, prefer an isolated uv installation:

```sh
uv tool install 'archer[all] @ git+https://github.com/schonacin/archer.git'
```

Alternatives are `pipx install 'archer[all] @ git+https://github.com/schonacin/archer.git'`, `python -m pip install 'archer[all] @ git+https://github.com/schonacin/archer.git'`, or `uv tool install '.[all]'` inside a clone. Append `@TAG_OR_COMMIT` to the Git URL when reproducibility matters.

D2 is a separate executable. It is required for SVG, PNG, and PDF, while `archer render --format d2` works without it. Use `archer doctor` to inspect installed backends and layouts. Archer does not download executables during ordinary commands.

## Commands and all flags

Archer has seven commands: `scan`, `render`, `context`, `diff`, `check`, `doctor`, and `skill`. Use `archer --version` for the package version. The top level and every command accept `-h`/`--help`.

The following shared flags apply to `scan`, `render`, `context`, `diff`, and `check`:

- `--root PATH`: repository; defaults to `ARCHER_ROOT`, then the current directory.
- `--source-root PATH`: repository-relative import root; repeatable; otherwise auto-detects `src`.
- `--exclude GLOB`: repository-relative source exclusion; repeatable. Quote globs.
- `-o PATH` / `--output PATH`: explicit output; `-` means stdout. Scan, render, context, and diff otherwise use descriptive files under `./archer/`; check defaults to stdout.

`--graph FILE` is available on `scan`, `render`, `context`, and `check`, and reads canonical JSON instead of scanning. It is not available on `diff`. Scan-related flags have no effect on the content of a supplied graph.

`render`, `context`, `diff`, and `check` accept one mutually exclusive Git mode:

- `--staged`: `HEAD` to index.
- `--worktree`: `HEAD` to working tree.
- `--unstaged`: index to working tree.

`--graph` cannot be combined with these modes. With no mode, `diff` and `--changes` compare `HEAD` with the working tree.

### `scan`

Create canonical JSON with nodes, relationships, resolution evidence, and diagnostics. It accepts the shared flags and `--graph FILE`.

### `render`

Render a graph using the shared flags, `--graph`, Git modes, and:

- `--level modules|types|symbols|full|changes`: detail level; default `modules`.
- `--changes`: use a Git-change neighborhood while retaining the selected level.
- `--radius N`: nonnegative focus/change distance; default `2`.
- `--direction both|incoming|outgoing`: neighborhood traversal; default `both`.
- `--focus MODULE_OR_SYMBOL`: exact ID or qualified name to center.
- `--exclude-arrows MODULE`: hide incoming and outgoing arrows for a module subtree; repeatable.
- `--exclude-arrows-to MODULE`: hide arrows entering a module subtree; repeatable.
- `--exclude-arrows-from MODULE`: hide arrows leaving a module subtree; repeatable.
- `--color-arrows true|false`: use the source subsystem color (default true); Git diffs retain change colors.
- `--svg-optimization raw|medium|fast`: SVG processing; default `fast`. Raw preserves D2 bytes, medium uses clips and bounded masks, and fast uses approximate maskless clipped paths where supported.
- `--no-optimize-svg`: alias for raw, mutually exclusive with `--svg-optimization`.
- `--format d2|svg|png|pdf`: output format; default `svg`.
- `--layout auto|tala|elk|dagre`: D2 layout; default `auto`.
- `--external`: include unresolved/external nodes in projected views; full already includes them.

Module diagrams aggregate parallel relationships and express package/module containment through nesting. Arrow exclusions retain nodes and apply to the named module and descendants at every level. Use explicit `-o` paths when stable filenames matter; otherwise Archer records focus, changes, direction, arrow exclusions, coloring, and nondefault SVG modes in generated names.

### `context`

Produce concise Markdown with hierarchy, locations, incoming/outgoing dependencies, changes, cycles, and coupling. It accepts the shared flags, `--graph`, Git modes, and the same `--level`, `--changes`, `--radius`, `--direction`, and `--focus` flags as `render`. `--max-chars N` sets the hard output budget and defaults to `12000`.

### `diff`

Create a semantic Git graph diff. It accepts zero or two positional revisions, the shared flags, and Git modes, but not `--graph`. Use `archer diff REV_A REV_B`, or omit revisions for `HEAD` to working tree. Deleted symbols remain represented.

### `check`

Check cycles and fan-in/out, plus baseline changes when requested. It accepts the shared flags, `--graph`, Git modes, and:

- `--baseline REV`: compare that revision with the working tree; mutually exclusive with Git modes.
- `--fan-threshold N`: nonnegative fan threshold; default `15`.
- `--coupling-threshold N`: allowed coupling increase; default `0`.
- `--format text|json`: output format; default `text`.

Use `archer check --worktree` after edits when architecture validation is requested. Exit `1` means findings, and exit `2` means an operational error or incomplete scan. Checks require an ordinary graph, not a diff graph.

### `doctor` and `skill`

`doctor` supports `--format text|json` and reports Archer, Git, D2 layouts, TALA, ty, and Pyright. `skill` has no command-specific flags and prints this bundled skill.

## Output and interpretation

Artifact commands write below `./archer/` in the current working directory unless `-o` is supplied. `ARCHER_ROOT` changes the default scan root, not the output directory. Incomplete scans still emit partial output and exit `2`.

Optional `archer.yaml` in the selected repository supplies shared and per-command flag defaults; explicit CLI flags override them. Use long flag names without `--`, booleans for switches, and lists for repeatable flags. Optional `.archerignore` supplies Git-style source exclusion patterns for scans and all Git snapshots.
