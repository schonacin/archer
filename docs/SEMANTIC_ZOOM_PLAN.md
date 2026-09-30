# Archer semantic zoom: implementation plan for GPT-5.6 Sol

Status: architecture specification; no feature implementation accompanies this document.
Date: 2026-09-19. Repository baseline: `8947ea4`.

## 1. Outcome and governing decisions

Deliver one portable `.html` file containing a repository module SVG, a type SVG for every included module, and a symbol SVG for every included class. Opening it directly with `file://` must provide smooth pan, zoom, selective semantic descent, ascent, and breadcrumbs without a server, network, browser D2, or runtime analysis.

The user's request governs scope. The attached `archer-semantic-zoom-context.md` is design input, not an independent instruction source. Its hierarchy, build-time layout, stable identity, and spatial-continuity proposals are adopted here with the specific amendments below. Its illustrative IDs and thresholds are not existing Archer contracts.

| Decision | Specification | Reason |
| --- | --- | --- |
| Scene architecture | Independent pre-rendered SVGs plus a versioned manifest | Bounds live DOM and keeps analysis in Python |
| Default sizing | Bottom-up child-derived aspect ratios, quantized into four rectangle buckets | Better continuity than squares without unconstrained overview geometry |
| Alternative sizing | One build-time `--html-node-shape square` option | Predictable uniform overview and comparison baseline |
| Scene transition | A child expands from its parent node through a clipped rectangular portal | Preserves spatial origin even when internal layouts differ |
| Arrow transition | Fade complete outgoing connectors, then reveal complete incoming connectors | Avoids invented one-to-one relationships and unstable route morphs |
| Runtime | Small packaged vanilla JavaScript/CSS, inlined into output | No CDN or mandatory Node toolchain for users |
| Layout | Reuse existing `auto` policy; explicit TALA is the reference visual configuration | Preserves documented fallback and dense-graph behavior |
| Identity | Exact existing canonical IDs, with a separate SVG-safe token map | No graph schema change or reconstructed IDs |
| Initial support | Ordinary snapshot graphs, full hierarchy within the selected input scope | Diff semantics are a separate feature |

Do not implement runtime layout, arbitrary edge morphing, a graph editor, source navigation, search, dependency highlighting, browser D2/WASM, remote scene loading, multiple live expanded modules, or multiple pre-rendered layout variants per scene in this version.

## 2. Evidence and research implications

The repository README is the primary product contract. Limited source inspection checked only integration surfaces: graph dataclasses, projection signature/behavior, D2 emission/rendering, function names, and packaging configuration. This is not a complete source audit. Sol must verify the touched call sites before editing.

Verified locally:

- The graph has canonical `Node.id` strings and relationship keys `(source, target, kind)`. Preserve occurrence suffixes. Do not introduce the attachment's `module:`/`type:` prefixes into entity IDs.
- `project(..., level="types")` currently keeps modules, packages, and classes. Running this repeatedly with different `--focus` values would not produce strictly owned scenes: focus is a dependency neighborhood.
- `d2_source()` projects internally; a scene already projected for ownership must not be projected again accidentally.
- `render()` chooses layout engines and optimizes SVG after D2. Its dense-scene threshold is over 500 nodes or 250 edges. Preserve this policy for `auto`.
- Docker and the installed D2 use 0.9.0. Archer's existing optimization preserves connector geometry but can introduce extra SVG elements.

Research-backed constraints:

1. D2 supports width/height for ordinary nodes and TALA/ELK containers, but TALA can enlarge containers to fit their contents. Dimensions are not a promise of exact container geometry. Use measured output for transitions. [D2 dimensions](https://d2lang.com/tour/dimensions)
2. TALA can change a layout substantially when input changes; fixed seeds and identical input support repeatability. Independent scenes should preserve their parent rectangle, not attempt to align all their interior nodes. [TALA announcement](https://d2lang.com/blog/tala-is-open-source/)
3. D2 documents encoded shape-ID CSS classes and per-diagram identifier prefixes. These provide a feasible adapter mechanism, but remain renderer details to isolate and fixture-test. [D2 SVG export](https://www.d2lang.com/tour/exports/)
4. Native SVG path interpolation requires matching path-command structures. Even normalizing paths would not solve aggregate-edge splitting or truthful endpoint correspondence. This supports the staged-fade decision. [SVG path animation](https://www.w3.org/TR/SVG/paths.html)
5. Uniform `meet` fitting preserves aspect ratio; stretching would deform labels and arrowheads. Letterboxing is intentional. [SVG preserveAspectRatio](https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/preserveAspectRatio)
6. `getBBox()` returns local geometry and does not apply ancestor transforms. Screen conversions need explicit matrices. [SVG getBBox](https://developer.mozilla.org/en-US/docs/Web/API/SVGGraphicsElement/getBBox), [getScreenCTM](https://developer.mozilla.org/en-US/docs/Web/API/SVGGraphicsElement/getScreenCTM)
7. Animation must use elapsed time, not frame counts. Wheel input requires normalization and deliberate cancellation; pointer events support two-pointer pinch. [requestAnimationFrame](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame), [wheel events](https://developer.mozilla.org/en-US/docs/Web/API/Element/wheel_event), [pinch gestures](https://developer.mozilla.org/en-US/docs/Web/API/Pointer_events/Pinch_zoom_gestures)
8. D3's zoom interpolation addresses smooth camera travel, but does not implement semantic scene substitution. Do not add all of D3 for this feature. Use the explicit interpolation below; revisit D3 only for future long-distance navigation. [D3 zoom interpolation](https://github.com/d3/d3/blob/main/docs/d3-interpolate/zoom.md)

The sizing, timings, thresholds, and performance budgets below are design decisions and initial acceptance targets, not claims established by those sources.

## 3. Scene coverage and projection semantics

### 3.1 Define the included graph once

Load or scan once through existing command behavior. Apply focus/radius/direction once, retaining ancestor containers for orientation. Compute initializer visibility and subsystem colors from the same information used by existing diagrams. Build every scene from this common included graph. No repeated scans and no browser-side projection.

With no scope filters, include every visible internal module and class. “Every” means every entity remaining after explicit input filtering and documented initializer suppression. `--initializers all` restores those suppressed leaves. Record hidden initializer counts in the UI. Ancestor-only containers restored by focus must not acquire unrelated descendants.

Build ownership indexes once from canonical containment relationships. Reuse existing established ownership helpers if suitable; do not add qualified-name splitting in the browser. Validate class/module ownership. A malformed saved graph with ambiguous containment must produce an actionable operational error rather than guess.

### 3.2 Required scenes

| Scene | Visible detail | Children |
| --- | --- | --- |
| Root modules | Existing module/package hierarchy and aggregated dependencies | One type scene per visible source module |
| Module types | Every class owned by that module, including nested/function-local classes; classes are collapsed cards | One symbol scene per class |
| Class symbols | The class's owned methods/functions, including nested functions beneath those members | None in this release |

Type scene cards are flat with owner-relative qualified labels, so `Outer.Inner` is distinct from `Outer`; the containing class name is also accessible text. This is an intentional HTML-specific presentation: it makes every class a measurable expandable leaf, including classes that own other classes. Preserve ownership in the manifest, rather than turning parent classes into large nested containers in this scene.

A class symbol scene includes declarations whose nearest enclosing class is that class. Descendants of a nested class belong in the nested class's own symbol scene, accessible from its module's type scene. This prevents duplicate membership and keeps the scene hierarchy a tree of depth three. Show a noninteractive count of nested classes and explain where they are available. Do not count inherited methods as declarations unless they actually exist as extracted owned nodes.

Module-level free functions are not classes. They do not receive fabricated class IDs or extra symbol scenes. The module view must state their count and explain that this release drills into classes only. They still contribute to existing aggregate module relationships. A future module-symbol scene is explicitly outside this request's three-level scope.

Packages without a source-module identity remain navigational grouping containers, not fabricated modules. Package initializer/module identity overlap must follow Archer's existing distinction: attach the child scene to the exact source-module visual, never an arbitrary enclosing package. If an initializer visual is suppressed, it has no child link.

Empty module and class scenes are valid SVGs with a clearly labelled empty-state card. They remain enterable; metadata reports `entityCount: 0`. Do not omit them or replace them with missing-scene errors. The scene count invariant is `1 + M + C`, where M and C are included visible source modules and their classes.

### 3.3 Relationship policy

Containment is represented by nesting or metadata, not dependency arrows. Within a module scene, aggregate relationships to visible class owners using established Archer resolution/confidence rules. Within a class scene, retain relationships among visible members and their nested functions. Class-boundary endpoints for relationships owned by the class itself use a small labelled owner/header endpoint, consistent with the existing renderer's ancestor/descendant workaround.

For relationships crossing a scene boundary, emit terminal context nodes grouped by the other endpoint's owning module; for another class in the same module, group by that class. Label them `Outside this view: …`, use subdued styling, preserve direction, and keep them nonexpandable. Both inbound and outbound edges are included. Internal context nodes are available regardless of `--external`; that flag controls actual external/unresolved graph entities. Those targets remain distinct and retain uncertainty styling when enabled.

An endpoint represented by a context node is never presented as the actual called method. Store its original endpoint provenance. Context nodes are synthetic scene objects with `entityId: null`; they cannot create more scenes. Do not silently omit cross-boundary edges or pretend the child diagram has no outside dependencies.

Apply arrow exclusions to original endpoints before grouping so module-subtree exclusions remain correct. Preserve relationship kinds, source subsystem colors, uncertainty/dashes, unique relationship counts, and call-site counts separately. A combined displayed connector keeps its contributing canonical edge keys in build-time metadata; the runtime only needs the displayed endpoints, kinds, and counts. No call-site source text is necessary in the HTML.

## 4. CLI and compatibility contract

Add:

```sh
archer render --format html
archer render --format html --layout tala -o architecture.html
archer render --graph graph.json --format html --html-node-shape square
archer render --format html --focus acme.render --radius 1
```

- Extend format validation, YAML defaults, help, output suffix validation, default naming, and stdout handling.
- Default HTML name: `archer/architecture-explorer.html`, with existing scope/arrow/style suffix conventions. Square mode appends `-square`; the default bucket mode does not.
- `--html-node-shape bucketed|square`, default `bucketed`, is HTML-only. An explicitly supplied HTML-only flag with another format is an error. Configured defaults must be handled consistently with existing command validation and documented.
- For HTML, `--level modules` is accepted, including the default. Other level values are rejected with a message that HTML always contains the complete three-level hierarchy. Do not silently reinterpret them as the starting scene.
- Reject `--changes`, Git snapshot comparison switches, and saved diff graphs for HTML with an operational error. Ordinary `--graph` input and ordinary filesystem scans are supported. Existing formats retain their current behavior.
- Reuse `--external`, initializer policy, focus, arrow exclusions, arrow coloring, and SVG optimization. `raw` means no geometry optimization, not bypassing necessary identity annotation, identifier namespacing, or safe packaging.
- `auto` retains existing per-scene layout selection and fallback. Explicit `tala|elk|dagre` stays explicit. Record the actual engine and warnings per scene; never silently replace an explicit layout.
- Missing D2, unsupported SVG structure, failed scene rendering, or invalid manifest: fail with exit 2 and identify the scene and engine. No successful-looking artifact with missing scenes.
- Preserve the README's partial-scan contract: a valid partial graph may yield an explorer displaying its diagnostics, with command exit 2. Distinguish extraction incompleteness from a broken HTML export.
- Buffer/build to temporary output and atomically replace the destination after validation. For stdout, emit only the complete HTML; progress and warnings go to stderr.

The reference environment is D2 0.9.0. Require at least that version for HTML initially, while preserving existing version behavior for other formats. Do not declare all future D2 versions compatible merely from their version number: validate adapter invariants on each output and fail clearly if violated.

## 5. Build pipeline and code boundaries

Suggested boundaries are implementation targets, not claims that these modules already exist:

| Location | Responsibility |
| --- | --- |
| `src/archer/cli.py`, `config.py` | Options, validation, output routing |
| `src/archer/render/scenes.py` (new) | Scene inventory, ownership, per-scene projections, provenance |
| `src/archer/render/__init__.py` | Reusable D2 emitter/render primitive; preserve public static rendering behavior |
| `src/archer/render/svg_scene.py` (new) | D2 SVG adapter, semantic annotations, geometry, identifier isolation |
| `src/archer/render/html.py` (new) | Manifest validation and single-file packaging |
| `src/archer/render/assets/viewer.js`, `viewer.css` (new) | Camera, input, transitions, accessible controls |
| `tests/test_html_*.py` (new) | Projection, adapter, CLI, artifact tests |
| `tests/browser/` (new) | Browser navigation, offline, screenshots, performance harness |

Execution order:

1. Validate options/tool availability; load one graph; compute scope, ownership, colors, and scene inventory.
2. Build all scene projections and synthetic boundary nodes deterministically.
3. Render every class symbol scene. Measure its SVG `viewBox`; choose the corresponding class-card dimensions.
4. Render every module type scene using those dimensions. Measure its `viewBox`; choose corresponding module-card dimensions.
5. Render the root module scene using the module dimensions.
6. For each SVG: verify and annotate identity, optimize according to the requested mode, validate annotation retention, namespace/isolate SVG resources, and serialize.
7. Validate manifest coverage and child links; package runtime and scene strings; write atomically.

Only three dependency waves are required. No iterative global layout solver. Use sequential rendering initially for predictable memory; do not introduce unbounded subprocess concurrency. Preserve the existing per-engine timeout and report `scene i/N` on stderr. User cancellation cleans temporary files.

Factor the emitter so it can receive an explicit prepared scene and a presentation map without re-running generic projection. Static export callers continue to use their existing projection path. Reuse style and edge-formatting functions instead of cloning them. Neither canonical graph objects nor ordinary SVG defaults may be mutated by HTML presentation decisions.

Store SVGs in temporary files during the build. Stream the final scene dictionary into the output rather than retaining multiple copies of all SVG strings. A persistent scene cache is deferred; deterministic ordering and recording renderer/options are required now.

## 6. Shape and size specification

### 6.1 Tradeoffs

| Approach | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Fixed squares | Uniform overview; independent scene scheduling | Substantial empty space for wide/tall children | Supported alternative |
| Unrestricted child aspect ratio | Closest bounding-box match | Extreme node shapes and unstable parent layouts | Reject |
| Clamped continuous ratios | Better fit with bounded extremes | Many arbitrary dimensions and harder visual comparison | Defer |
| Four child-derived buckets | Consistent vocabulary and improved fit | Three build waves; still some letterboxing | Default |
| Multiple layout variants | Could adapt to viewport | Larger artifacts, extra layout costs and navigation identity complexity | Defer |

### 6.2 Deterministic rules

For a child SVG's finite positive `viewBox` dimensions W,H, set `r = W/H`. Clamp to `[0.75, 2.0]`, then choose the nearest bucket in logarithmic space from `{0.75, 1.0, 1.5, 2.0}`. Resolve exact ties toward 1.0, then the smaller bucket. Square mode always chooses 1.0.

For an expandable card, start with area A = 40,000 D2 units squared:

```text
width  = sqrt(A * bucket)
height = sqrt(A / bucket)
```

Round upward to integer units. Approximate bucket sizes are 174×231, 200×200, 245×164, and 283×142. If a two-line label plus kind badge does not fit, uniformly enlarge both dimensions; never change only one dimension to fit text. Initial maximum label is two lines of 24 displayed characters each, then ellipsis; preserve the full name in accessible text and the details panel. D2 text measurement is authoritative for final fit. If D2 enlarges the card anyway, use its actual boundary.

Keep module/package square corners, class rounded corners with stronger border, and member softer rounded corners. “Square mode” describes aspect ratio, not removal of class rounding. Nonexpandable method/function nodes keep compact existing styles; inflating all methods into squares would make the leaf scenes needlessly large.

Do not enforce child ratios on package grouping containers. They have many children and cannot reliably match a single scene. Put the legend and current-scene title in the fixed HTML UI so they do not bias measured scene aspect ratios. Use the rendered SVG viewBox, including actual layout padding, as the fit rectangle; no text-dependent browser measurement is needed to size parent scenes.

For extreme child ratios such as 6:1, preserve the whole diagram with padding inside the selected bucket. Never distort, crop, or lay out a scene again at runtime. Full view remains fit-to-window with manual zoom available. Record letterbox occupancy for visual evaluation; empty space is preferable to unreadable deformation.

## 7. SVG identity, geometry, and isolation

Maintain three separate concepts:

- `entityId`: exact canonical Archer ID, meaningful across scenes.
- `sceneId`: opaque stable token derived from `(scene kind, owner entity ID)`; root is reserved.
- `visualId`: scene-local safe token for a node, edge, context node, or owner endpoint.

Emit stable SVG-safe D2 identifiers using collision-checked digest tokens and retain an explicit token-to-entity map. Do not use display labels as keys. Decode/match D2's documented identity classes in one versioned adapter, then add `data-archer-id`, `data-visual-id`, and `data-role`. A node can have a body and separate header endpoint; map both deliberately. Graph IDs need not be valid CSS selectors: use maps and attribute values, not string-built selectors.

The adapter must identify:

- Node boundary geometry separately from its text or surrounding container.
- Node body, label, and decorations as one visual unit where appropriate.
- Every connector's route, arrowheads, label, mask/clip-dependent copies, and decorations as one animation unit.
- Noninteractive backgrounds and definitions without treating them as entities.

Prefer explicit rectangle geometry from D2's output for expandable cards. Apply nested SVG transforms to get a scene-space rectangle. If rounded bodies are represented as paths, support only the confirmed D2 0.9.0 rounded-rectangle form and fixture-test it; do not write an unrestricted SVG path layout engine. Unexpected expandable boundary geometry, skew, or rotation fails the build with a precise diagnostic; translation and uniform scale are the supported scene transforms. Browser `getBBox()` is a development cross-check, not a required build-time browser dependency.

Annotate complete edge groups before optimizing and verify all generated connector copies remain under the same group or receive equivalent role membership. If the optimizer prevents this, extend its HTML adapter integration while retaining static export behavior. Do not regroup nodes and connectors across paint-order boundaries merely to create a convenient layer: preserve D2's draw order, clips, masks, and ancestor styles. Store arrays of role elements for opacity changes instead.

Namespace every SVG ID with the scene token, including `url(#...)`, `href`/`xlink:href`, style references, clip/mask/marker references, and accessible ID references. D2's hash prefix alone is insufficient when identical empty scenes repeat. Scope diagram styles to a unique scene wrapper; rewrite keyframe/font names if present, or verify shared definitions are identical. Reject unexpected CSS constructs instead of applying an unsafe global regex. Existing broad CSS rules must not affect the HTML toolbar or another mounted scene.

No re-routing, resampling, or nonuniform scaling of connectors. Preserve the original marker geometry, dash pattern, and source coloring. Do not add `vector-effect: non-scaling-stroke` only to paths: it can create stroke/arrowhead inconsistency. In version one, the complete SVG scales uniformly. [SVG marker rules](https://www.w3.org/TR/SVG2/painting.html#VertexMarkerProperties)

## 8. Manifest and packaging contract

Use manifest schema version `1`, independently of graph schema `1.0`. Example (IDs and dimensions illustrative):

```json
{
  "schemaVersion": 1,
  "rootSceneId": "root",
  "build": {
    "archerVersion": "0.1.1",
    "d2Version": "0.9.0",
    "nodeShape": "bucketed",
    "svgOptimization": "fast",
    "incompleteScan": false
  },
  "scenes": {
    "root": {
      "level": "modules",
      "ownerEntityId": null,
      "parentSceneId": null,
      "parentVisualId": null,
      "viewBox": [0, 0, 1600, 900],
      "layout": "tala",
      "entityCount": 1,
      "nodes": [{
        "visualId": "n_a1",
        "entityId": "acme.renderer",
        "kind": "module",
        "bounds": [200, 120, 245, 164],
        "childSceneId": "s_b2",
        "label": "renderer"
      }],
      "edges": []
    },
    "s_b2": {
      "level": "types",
      "ownerEntityId": "acme.renderer",
      "parentSceneId": "root",
      "parentVisualId": "n_a1",
      "viewBox": [-20, -20, 900, 600],
      "layout": "tala",
      "entityCount": 0,
      "nodes": [],
      "edges": []
    }
  }
}
```

Each real edge record contains `visualId`, `sourceVisualId`, `targetVisualId`, `kinds`, `relationshipCount`, and `siteCount`. Each scene additionally records diagnostics, context-node counts, ownership metadata required for accessible labels, and any layout fallback messages. Synthetic empty-state graphics need not be semantic nodes.

Validate positive finite geometry, exact scene inventory, unique visual IDs, existing edge endpoints, reciprocal parent/child references, acyclicity, correct levels, and exactly one canonical parent per child. IDs must never depend on enumeration order or filesystem temporary paths.

Embed manifest and SVG strings as inert JSON in script data blocks, escaping `<` as `\u003c` and escaping script-sensitive Unicode separators. This avoids parsing all inactive SVGs into DOM at startup, unlike hundreds of template trees. Parse a requested SVG only when mounting it. JSON parsing still costs O(total file size); do not describe this as constant-memory loading.

Inline one classic runtime script and one stylesheet from packaged assets. No `fetch`, remote imports, worker URLs, service worker, telemetry, or localStorage requirement. Fonts and any supported resources must be embedded. Remove D2-generated external navigation for this export. Reject scripts, event-handler attributes, external-resource URLs, and unsupported active SVG content at build time; render repository names through text APIs/escaped XML. Verify `</script>`, quotes, ampersands, Unicode, and markup-looking names cannot escape their data context.

Mount one active scene while idle and at most two during a transition. Detached SVG elements are not retained in an unbounded cache. Keep only strings and small camera-history records. When needed, prepare a target invisibly in the second slot; do not start movement until parsing and font readiness finish. If preparation takes over 100 ms, display a quiet “Preparing view…” status while the current scene remains interactive.

## 9. Camera and portal mathematics

Use CSS pixels for all input and viewport measurements; devicePixelRatio does not multiply gesture thresholds. Scene units come from the SVG. Set transform origin to `(0,0)` and account for nonzero viewBox origins explicitly.

A camera maps scene point x to screen point s:

```text
s = k*x + t       k > 0, t = (tx, ty)
```

Let the selected node's scene-space boundary be P. Its portal interior I is P inset by 8 scene units on each side, clamped to at most 10% of each dimension. Let child viewBox V be `(vx,vy,vw,vh)`.

The child-to-parent embedding E is uniform:

```text
a = min(I.width / vw, I.height / vh)
bx = I.x + (I.width  - a*vw)/2 - a*vx
by = I.y + (I.height - a*vh)/2 - a*vy
E(x) = a*x + b
```

If parent camera is `(kp,tp)`, the equivalent child camera is:

```text
kc = kp*a
tc = kp*b + tp
```

Its inverse rebase is `kp = kc/a`, `tp = tc - kp*b`. Matrix equality, not eyeballing, determines continuity. Add unit tests with nonzero/negative origins and letterboxing. At scene commit, changing which scene is active must move corresponding pixels by no more than 0.5 CSS px.

For viewport W,H excluding toolbar, fit the child to 32 CSS px padding on each side (use 16 px below 480 px viewport width). The fit camera uses the same uniform fit formula. On entry, animate parent camera from the current value to `CchildFit * inverse(E)` while the child always uses `Cparent(t) * E`. The parent may grow very large but fades away early. Both layers share one spatial model throughout. Idle camera scale limits do not clamp these intermediate portal transforms; apply limits only to the committed active scene.

Interpolate log scale and scene-space camera center with smoothstep `q=3u²-2u³`, where `u` is elapsed/duration clamped to `[0,1]`. Recover translation from the viewport center each frame. This avoids abrupt scale velocity without bounce/overshoot. Explicit entry centers the child; ordinary wheel zoom before transition stays pointer-anchored. No animation of individual node coordinates.

The portal clip is the transformed P boundary. It shares the selected card's corner shape; the child is fitted inside I. A temporary shell uses the selected card's fill/border and replaces its body visually while details appear. At transition start, hide the original body exactly when the identical shell appears; keep its label independently controlled by the fade schedule. Clip any huge portal to the viewport; fade its border away before it becomes an enormous frame. Child SVG background must not cover parent context outside the portal.

Store the parent camera at entry plus E and the selected visual ID. On exit, derive the starting parent camera from the *current* child camera and inverse(E), then animate to the stored parent camera; the child again follows `Cparent(t)*E`. This handles child panning and zooming without snapping to an old child pose. Remove the child only after the reverse reveal completes. Back restores the parent's prior framing and selected node. If viewport size changed, restore the saved scene center and scale adjusted by the ratio of old/new viewport fit scales, then clamp to normal limits.

## 10. Semantic navigation and input

### 10.1 Candidate and threshold rules

Only one candidate exists. Priority is an explicitly selected expandable node, otherwise the deepest eligible node under the pointer, otherwise the eligible node nearest the viewport center. Center fallback requires its center to lie inside the middle 50% of the viewport. Require at least 50% of its boundary area to be visible; group containers without child scenes are ineligible.

Measure occupancy using both dimensions:

```text
occupancy = max(nodeScreenWidth / viewportWidth,
                nodeScreenHeight / viewportHeight)
```

This replaces the attachment's fixed 600/400 px examples so portrait windows and wide nodes behave consistently. Trigger entry when occupancy crosses 0.68 during zoom-in and the candidate has remained stable for 100 ms. Candidate selection alone, initial fit, resize, and panning cannot trigger entry. Explicit Enter/double-click/Open bypasses the threshold. Empty child scenes obey the same rules.

Child scale is in different units, so do not apply the parent's exit pixel threshold directly after rebasing. Record `entryFitScale` on child commit and recompute its viewport-fit reference after resize without triggering navigation. Auto-exit requires zoom-out with `currentScale / entryFitScale < 0.62` for 100 ms. This expresses child-screen occupancy relative to the fit pose and is independent of parent aspect ratio. Back/Escape exits immediately through the same transition.

After any commit, suppress another automatic semantic transition until a new gesture (wheel idle gap 180 ms, or pointer release/new pinch). After returning to a parent, that node is disarmed until occupancy drops below 0.50 or explicit entry occurs. This prevents automatic re-entry when the restored camera is still above 0.68. A single long inertial gesture must not cascade from root to symbols. Root has no exit transition; leaf scenes have no entry transition.

### 10.2 Input behavior

- Wheel zoom anchored at pointer; normalize deltaMode: pixel=1, line=16 px, page=viewport height. Clamp each normalized event delta to ±120 px and apply `kNew=kOld*exp(-0.002*delta)`. Trackpad pinch wheel events may carry ctrlKey; handle those inside the diagram. Leave browser toolbar/keyboard page zoom intact.
- Pointer drag pans; capture the pointer and release on up/cancel. A movement over 5 CSS px suppresses click selection.
- Two touch pointers pan at their midpoint and zoom by distance ratio, preserving the point beneath that midpoint. Apply `touch-action: none` only to the diagram region.
- Coalesce input and paint once per requestAnimationFrame. Use time-based damping toward the input target with initial time constant 60 ms; no independent CSS transform animation. Clamp scales to `[0.15,32] * currentSceneFitScale`; semantic exit is evaluated before minimum-scale clamping can prevent it.
- Clicking selects a node; double-click/tap enters it. Buttons: Back, Fit, zoom in, zoom out, and Open selected. Fit affects only the current scene and cannot trigger entry.

## 11. Transition choreography and arrow quality

Default duration 360 ms, reverse 300 ms. Keep constants in one runtime configuration object. Use the same normalized progress p for camera, clip, shell, and opacity; no separate timers.

| Entry progress | Visual behavior |
| --- | --- |
| 0–0.20 | Parent connectors fade 1→0 as complete units; selected shell remains stable |
| 0.08–0.35 | Sibling nodes and selected parent label fade out; shell grows with the camera |
| 0.20–0.65 | Child node bodies and labels fade 0→1 inside the portal |
| 0.55–0.90 | Child connectors fade 0→1, including labels and arrowheads |
| 0.65–1.00 | Shell fill/border recede; child becomes the sole active scene |

Apply smoothstep within each interval. By the time incoming arrows become visible, outgoing arrows are gone. This intentional separation prevents a double-route “spaghetti” frame. Reverse the choreography on exit: child edges vanish before parent arrows return, and parent nodes appear before their connectors. A connector must never remain visible without both relevant endpoints in that scene being visible.

Every arrow's path, markers, separately drawn head, label, and optimizer-created pieces share the same group opacity. Preserve uncertainty dashes; do not use animated dash offsets to simulate drawing, because dashes already encode meaning. No rubber-band edges, temporary stubs, shape-to-edge transformations, or linearly moving endpoints. A parent relationship can summarize dozens of child edges, so route morphing is both visually fragile and semantically misleading.

The persistent cues are the selected shell, subsystem color, and scene title moving into a stable breadcrumb. Parent arrows fade with their original attachment geometry; child arrows enter only at their already-routed geometry. This is the version-one definition of a good arrow transition, not a placeholder for later path morphing.

State machine: `idle → preparing → entering/exiting → idle`. One transition owns one animation frame loop and one cancellation token. During preparation, switching candidate cancels the stale preparation. During animation, queue/coalesce further wheel deltas for application after commit, but discard stale inertia after the 180 ms idle gap. Escape/Back reverses an entry from its current progress; retain both scene slots until reversal completes. Ignore duplicate Open activation while transitioning. Drag starts are deferred until commit (at most 360 ms). Breadcrumb jumps unwind ancestors sequentially, each no more than 160 ms, or instantly in reduced-motion mode.

On resize during a transition, stop at the current interpolated pose, commit the nearer endpoint (p<0.5 source, otherwise target), then refit without further semantic triggering. On hidden-tab return, finish/cancel based on elapsed time, never replay hundreds of stale frames. Runtime parse failure leaves the current view intact and provides a readable error.

Honor `prefers-reduced-motion`: disable automatic semantic descent/ascent and perform explicit navigation without spatial flight, using at most an 80 ms opacity change. All content remains available through keyboard and controls. [Reduced-motion preference](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion)

## 12. Accessibility and performance

Provide a visible scene title, breadcrumb buttons, full-name details for selection, and a short live announcement after scene commit. Graph visuals must have accessible names; provide a synchronized HTML list of the current scene's expandable nodes for reliable keyboard access rather than depending on SVG tab behavior across browsers. Enter opens, Escape/Back returns, arrows pan while the diagram has focus, +/- zoom, and Home fits. Restore focus to the activating node/list item after return. Color is supplementary to kind labels, border styles, and relationship labels.

Measure on a recorded reference laptop/browser, not an unspecified universal device. Acceptance targets for the reference fixture (up to 250 nodes / 250 connectors in the active scene; 200 embedded scenes; output up to 20 MiB):

- Local open to usable root under 2 seconds.
- Cached-font scene preparation p95 under 100 ms.
- During warm pan/transition, p95 frame interval at most 20 ms on a 60 Hz display and no main-thread task over 50 ms caused by the animation loop.
- One mounted scene when idle, two maximum during transitions. No repeated geometry reads or XML parsing within animation frames.
- After 50 enter/exit cycles, mounted SVG count is stable and browser heap does not show linear growth after garbage collection.

These are targets to validate, not current measurements. Profile raw/medium/fast on identical scenes. DOM count alone does not eliminate painting costs from masks or densely connected SVGs. On a slow scene, retain pan/zoom and explicit navigation, report a performance warning, and use the reduced-motion transition path if needed; do not silently omit entities or arrows. Large scenes/files remain supported on a best-effort basis. Warn at export when total HTML exceeds 50 MiB or any scene exceeds 2,000 semantic nodes, reporting counts without truncating output.

## 13. Ordered implementation work packages

Complete the following gates in order. Each package should produce a reviewable change; do not label the task complete after the first working animation.

### P0 — prove the renderer adapter contract

Create tiny D2 fixtures for module card, rounded class card, nested package/module, class header endpoint, labelled/dashed connector, empty scene, and duplicate scenes. Render with pinned D2 0.9.0 and TALA. Verify identity-class decoding, boundary extraction, whole-connector membership, optimized copies, and resource isolation. Test negative viewBox origins and supported layout alternatives.

Exit: documented fixture contract and failing diagnostics for unsupported geometry. If D2 output does not supply unambiguous identity, use an emitter-owned quoted fragment link token as the secondary marker, fixture-test the resulting SVG wrapper, and strip navigation during normalization. Do not fall back to label matching or screen coordinates. If neither mechanism works, fix the adapter design before building the UI.

### P1 — prepare scenes and deterministic dimensions

Implement coverage/ownership rules, boundary context, inherited colors, arrow exclusions, and bottom-up bucket selection. Refactor only the necessary emitter boundary. Test exact inventory `1+M+C`, nested classes, empty modules/classes, free-function counts, suppressed initializers, and focused subsets.

Exit: correct independent SVGs with no browser involvement and no regression in static exports.

### P2 — package the offline artifact

Implement schema validation, resource isolation, JSON-safe embedding, packaged asset loading, HTML CLI path, errors, atomic output, and stdout. Add wheel/sdist checks for runtime assets; extend maturin include configuration if needed.

Exit: an installed wheel can emit one HTML that opens offline and displays its root. No CDN requests or missing resources.

### P3 — camera, navigation, and accessibility

Implement uniform camera math, pointer/wheel/touch normalization, candidate selection, thresholds, history, Fit, Back, and keyboard controls. Implement entry/exit with immediate switching first to verify routing, but do not ship this as the completed feature.

Exit: every scene reachable and returnable; camera rebase tests pass; no input-induced hierarchy skipping.

### P4 — portal and connector choreography

Implement clip/shell, common camera transforms, opacity roles, elapsed-time interpolation, reversal, cancellation, reduced motion, and resize handling. Capture actual transition frames at 0/20/50/80/100%, including reverse transitions after panning.

Exit: no arrow detachment, double-routed frame, distorted text, unexpected flash, or commit jump greater than 0.5 CSS px. Compare bucketed and square builds on the same fixtures; retain bucketed default unless the recorded visual results expose a concrete correctness problem.

### P5 — regression, browser, and release documentation

Run Python/CLI suites, actual D2 fixture tests, browser tests in Chromium/Firefox/WebKit, and packaging/offline checks. Use a separate pinned browser-test dependency group, such as Python Playwright, with documented browser installation for development/CI only. A normal Archer install must not download browsers. Add performance measurements and known limits to `docs/VALIDATION.md`; update README command, filename, requirements, and interaction sections.

Exit: all acceptance criteria below demonstrated. Report any untested real-device pinch behavior explicitly; desktop wheel simulation alone is insufficient evidence of touch support.

## 14. Required test matrix and definition of done

| Area | Cases and assertion |
| --- | --- |
| Coverage | Zero-node graph; module without classes; empty class; nested/local classes; repeated names; every required scene embedded exactly once |
| Semantics | Local calls, inheritance, class-owned edges, module free functions, nested methods/functions, cross-module dependencies, external/unresolved targets; no invented relationships |
| Filters | Focus scope/ancestor containers; initializers auto/all; each arrow exclusion; colors off; external on/off |
| Geometry | Square/portrait/wide/extreme-ratio scenes, very long labels, negative viewBox origin, transformed SVG groups; exact coordinate mapping |
| SVG integrity | Duplicate scenes, IDs, gradients/masks/clips, embedded fonts, arrow labels/heads, raw/medium/fast; simultaneous mounts visually identical to standalone scenes |
| Motion | Wheel and pinch entry, zoom-out exit, Back after pan, reversal mid-transition, repeated activation, resize, tab suspension, long inertial scroll |
| Safety/portability | `file://`, blocked network, no external requests, hostile-looking labels, non-ASCII IDs, no storage permission, copied HTML on another machine |
| CLI | Missing D2, wrong extension, incompatible flags, saved diff, per-scene timeout, stdout purity, no partial replacement on failure, incomplete scan exit 2 |
| Accessibility | Keyboard-only traversal to every child; correct focus restoration; reduced motion; selected full names; current-scene announcement |
| Performance | Scene/DOM bounds, frame measurements, 50-cycle memory test, documented file sizes and build time |

Required integration fixture: at least two packages, three modules, four classes including one nested class, one class without members, methods with nested functions, a free function, one cross-module call, inheritance, one unresolved reference, and a long Unicode label. Add separate tall/wide/dense generated fixtures so layout extremes are repeatable.

Use transform/state assertions for animation correctness and a small set of reviewed visual screenshots for appearance; do not make cross-platform exact SVG bytes or pixel-perfect antialiasing snapshots the only tests. Performance tests should record environment and use broad CI guards; visual smoothness still requires a real interactive inspection.

Definition of done:

- The requested single HTML contains the full required scene inventory and works without network access.
- Every included module/class can be entered and left with mouse, touch, and keyboard controls.
- Transitions preserve spatial origin and uniform geometry; complete arrow units fade in the specified order.
- Both sizing modes work; bucketed is the documented default; no runtime layout variants are generated.
- Scene isolation survives two simultaneous SVGs; mounted DOM and history remain bounded.
- Existing static rendering behavior and SVG optimization guarantees remain intact.
- README, validation evidence, wheel packaging, and error behavior are complete.

## 15. Handoff instructions for Sol

Treat this document as the implementation specification. Begin with P0 and inspect only the code needed for each work package. Preserve canonical graph semantics and existing static formats. Centralize tunable constants; do not scatter thresholds or durations through event handlers. If a verified renderer/browser fact contradicts this plan, record the evidence, the smallest design amendment, and its consequences before broad implementation. Routine implementation choices do not need another product-design round.

The architectural priority is truthful graph semantics, then spatial continuity, then visual polish, then optimization. The feature is complete only when all three semantic levels, reverse navigation, offline packaging, and arrow choreography work together.
