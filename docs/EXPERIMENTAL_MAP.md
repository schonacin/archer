# Experimental architecture map

Generate the new, opt-in viewer:

```sh
archer render --format html --html-viewer experimental --layout tala
```

The default output is `archer/architecture-explorer-experimental.html`. Open the
file in a browser. D3 7.9.0 and all geometry are bundled; the artifact makes no
network requests. D2 is needed at export time, not when exploring the file.
`--layout auto` also works and uses the existing layout fallback policy.

To compare with TALA positioning at every hierarchy level:

```sh
archer render --format html --html-viewer experimental --html-map-layout reference --layout tala
```

This writes `archer/architecture-explorer-experimental-reference.html`. The default
`--html-map-layout compact` remains available for the denser package overview.
Reference mode retains engine positions for packages and the repository root as
well as modules, classes and functions. Expandable card proportions are bounded
before layout to avoid very long child flows creating unreadable overview towers.
Geometry stays fixed while exploring; layout runs only during export.

The classic viewer remains the default. Its exporter and assets are unchanged.
The experimental exporter lives in `src/archer/render/experimental.py`; its HTML,
CSS, JavaScript, vendored D3 and license are under `render/assets/experimental/`.
`--html-node-shape` and `--html-view-mode` belong to the classic viewer and are
rejected when combined with the experimental setting.

## Exploring

- Scroll a mouse wheel to zoom toward the pointer. Low-amplitude pixel scrolling
  and horizontal scrolling pan; Ctrl-wheel trackpad pinch zooms. Touch supports
  dragging and two-finger pinch. Device wheel conventions vary, so high-resolution
  mice and trackpads should still receive hands-on testing.
- Double-click a map card to enter; click a region in the explorer to enter it.
  Package, module, class and nested function ownership all provide navigable
  levels. Top-level functions are first-class entities.
- Click an entity to inspect its source location and Uses / Used by relationships.
  Following a relationship moves to and selects the destination. Back restores
  the previous camera and selection.
- Search includes every exported entity, including nested functions and methods.
- Breadcrumbs and Escape ascend; F fits the current region; Home returns to the
  overview; Alt-Left goes back. Arrow keys pan in screen pixels. Plus/minus zoom;
  slash focuses search. The sidebar provides keyboard access to map entities.
- Entity types have consistent colors and silhouettes: teal folder outlines for
  packages, blue cards for modules, violet clipped corners for classes, and amber
  capsules for functions and methods. Type badges and the legend reinforce them.
- Connections are visible by default at 24% strength. The Strength slider adjusts
  background arrows from 0–80%; selection and hover stay strongly highlighted,
  even at zero. Selecting an entity also dims unrelated connections. Connections
  toggles background arrows without losing the slider setting.
- Arrows aggregate canonical relationships at the currently readable hierarchy
  frontier within the focused region. After zoom settles for 120 ms, endpoints
  blend into the new frontier over 180 ms. The frontier depends on magnification,
  not viewport intersection: dragging, trackpad panning and keyboard panning keep
  endpoint identities, geometry and the current region stable.
- Outside connections are summarized by a quiet "Outside this region" control.
  It opens a list in the explorer with exact destinations and Uses / Used by
  counts. Selecting an entity scopes the count to its outside connections, which
  are also marked in the normal inspector. These links navigate directly and Back
  restores context. There are no floating boundary tags or viewport-anchored rays.
- The minimap preserves the larger context and can reposition the camera.
- Reduced-motion preferences remove animated camera travel while retaining
  direct zoom and hierarchical detail.

A dominant top-level namespace is framed on initial entry, so useful subsystems
are visible immediately. The complete repository and tooling remain accessible
through the Architecture breadcrumb. Package initializer declarations are grouped
under an `__init__` region when the package also has submodules.

## Geometry and interaction

The canonical graph supplies IDs, ownership, provenance and relationships. The
exporter lays out each ownership neighborhood bottom-up using D2, extracts node
bounds and normalized connector paths through the existing validated adapter,
and writes structured map data. Finished D2 SVGs are not embedded in the viewer.

Modules, classes and functions preserve the reference node layout. In compact
mode, packages with several children use consistently sized cards ordered from
their reference layout, and the browser generates curves from canonical
relationships. Reference mode keeps the original engine geometry everywhere,
including its connector paths for sibling endpoints. During hierarchy refinement,
sampled reference paths follow the interpolated cards; settled endpoints restore
the exact engine path. Connections spanning different ownership neighborhoods
still use generated curves, which do not avoid obstacles. Connections outside
the focus remain navigable in the explorer. Dense graphs can still have crossings;
lowering Strength keeps the overview quieter without hiding selected relationships.

The browser embeds each neighborhood into its parent card once. One D3 camera
navigates these fixed coordinates. Child details fade in according to projected
screen size; ancestors remain spatially aligned. Explicit entry fits the content
bounds, rather than leaving it tiny inside the original card. Text and arrowheads
render in screen coordinates to remain crisp and readable at deep zoom. Off-screen
and unreadably small descendants are omitted from rendering. No layout runs
while navigating.

The SVG adapter shares the classic exporter's D2 version contract. D3 is pinned
and licensed in `D3-LICENSE`, with download provenance in `VENDOR.md`. The normal
render command never downloads dependencies.

## Validation and limits

`tests/test_experimental_render.py` checks ownership, initializer grouping,
relationship filtering, route coordinate normalization, escaping, isolated CLI
selection and portable output. `tests/browser/test_experimental_map.py` exercises
actual Chromium navigation, visual entity types, connection strength, settled
endpoint refinement, stable pan geometry and outside navigation, function-only
modules, dependency jumps and precise
history restoration, wheel descent/ascent, trackpad panning, touch pinch, keyboard
navigation, interruption, resize and reduced motion. Browser checks require
Playwright plus a Chromium executable; they load the standalone bytes with
`set_content` so enterprise `file://` policies do not interfere.

Reference mode also has checks for engine placement at every level, preservation
of exact connector paths, stable routed panning, selection and interrupted zoom.

The viewer exposes bounded `__ARCHER_MAP_METRICS__` samples for paint duration and
frame intervals, and a read-only `__ARCHER_MAP_STATE__()` snapshot for browser
checks. These are diagnostics, not product UI. Headless timing is not a substitute
for testing actual laptops and trackpads. This experiment does not support Git
diff graphs; it shares the HTML command's existing snapshot restrictions. Source
locations are shown without embedding source code or launching an editor.
