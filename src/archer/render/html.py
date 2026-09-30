"""Build the portable semantic-zoom HTML artifact."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from archer import __version__
from archer.render.scenes import assign_child_dimensions, build_scenes, scene_graph
from archer.render.svg import optimize_svg
from archer.render.svg_scene import adapt_svg, scene_source


def _json_data(value):
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _d2_version(executable):
    result = subprocess.run(
        [executable, "--version"], check=False, capture_output=True, text=True, timeout=10
    )
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout + result.stderr)
    if result.returncode or not match or tuple(map(int, match.groups())) < (0, 9, 0):
        raise ValueError("HTML export requires D2 0.9.0 or newer with the validated SVG identity contract")
    return ".".join(match.groups())


def _render_scene(executable, scene, directory, layout, timeout, optimization, color_arrows):
    source_path = directory / f"{scene.scene_id}.d2"
    rendered = directory / f"{scene.scene_id}.svg"
    source_path.write_text(scene_source(scene, color_arrows=color_arrows), encoding="utf-8")
    density = scene_graph(scene)
    dense = len(density.nodes) > 500 or len(density.edges) > 250
    engines = ["elk", "dagre"] if dense else ["tala", "elk", "dagre"]
    if layout != "auto":
        engines = [layout]
    errors = []
    for engine in engines:
        try:
            result = subprocess.run(
                [executable, "--layout", engine, str(source_path), str(rendered)],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{engine}: exceeded {timeout}s")
            continue
        if result.returncode == 0 and rendered.exists():
            data, stats = optimize_svg(rendered.read_bytes(), level=optimization)
            svg, viewbox, nodes = adapt_svg(data, scene)
            return svg, viewbox, nodes, engine, errors, stats
        errors.append(f"{engine}: {result.stderr.strip()}")
    raise ValueError(
        f"Scene {scene.scene_id} ({scene.title}) failed with layout {layout}: " + "; ".join(errors)
    )


def validate_manifest(manifest):
    if manifest.get("schemaVersion") != 1 or manifest.get("rootSceneId") != "root":
        raise ValueError("Invalid semantic-zoom manifest header")
    scenes = manifest.get("scenes")
    if not isinstance(scenes, dict) or "root" not in scenes:
        raise ValueError("Semantic-zoom manifest has no root scene")
    children = set()
    for sid, scene in scenes.items():
        viewbox = scene.get("viewBox", [])
        if len(viewbox) != 4 or not all(math.isfinite(x) for x in viewbox) or min(viewbox[2:]) <= 0:
            raise ValueError(f"Scene {sid} has invalid geometry")
        if scene.get("level") not in {"modules", "types", "symbols"}:
            raise ValueError(f"Scene {sid} has an invalid semantic level")
        if sid == "root":
            if scene["level"] != "modules" or scene.get("parentSceneId") is not None:
                raise ValueError("Root scene has invalid ownership")
        elif scene.get("parentSceneId") is None:
            raise ValueError(f"Scene {sid} has no canonical parent")
        if not isinstance(scene.get("nodes"), list) or not isinstance(scene.get("edges"), list):
            raise ValueError(f"Scene {sid} has invalid node or edge records")  # noqa: TRY004
        if any(
            not isinstance(node, dict) or not isinstance(node.get("visualId"), str) for node in scene["nodes"]
        ):
            raise ValueError(f"Scene {sid} has invalid node visual IDs")
        visuals = {node["visualId"] for node in scene["nodes"]}
        if len(visuals) != len(scene["nodes"]):
            raise ValueError(f"Scene {sid} has duplicate visual IDs")
        if any(
            not isinstance(edge, dict) or not isinstance(edge.get("visualId"), str) for edge in scene["edges"]
        ):
            raise ValueError(f"Scene {sid} has invalid edge visual IDs")
        edge_visuals = {edge["visualId"] for edge in scene["edges"]}
        if len(edge_visuals) != len(scene["edges"]):
            raise ValueError(f"Scene {sid} has duplicate edge visual IDs")
        for edge in scene["edges"]:
            if edge["sourceVisualId"] not in visuals or edge["targetVisualId"] not in visuals:
                raise ValueError(f"Scene {sid} has a dangling displayed edge")
        for node in scene["nodes"]:
            bounds = node.get("bounds", [])
            if len(bounds) != 4 or not all(math.isfinite(x) for x in bounds) or min(bounds[2:]) <= 0:
                raise ValueError(f"Scene {sid} node {node['visualId']} has invalid geometry")
            child = node.get("childSceneId")
            if child:
                if child not in scenes:
                    raise ValueError(f"Scene {sid} links to missing child {child}")
                target = scenes[child]
                if target["parentSceneId"] != sid or target["parentVisualId"] != node["visualId"]:
                    raise ValueError(f"Scene {child} has a non-reciprocal parent link")
                expected = {"modules": {"types"}, "types": {"symbols"}}.get(scene["level"], set())
                if manifest.get("build", {}).get("viewMode") == "isolated" and scene["level"] == "modules":
                    expected.add("symbols")
                if target["level"] not in expected:
                    raise ValueError(f"Scene {sid} has an invalid child semantic level")
                if child in children:
                    raise ValueError(f"Scene {child} has multiple canonical parents")
                children.add(child)
        frame_bounds = scene.get("frameBounds")
        if frame_bounds is not None:
            frames = [node for node in scene["nodes"] if node["role"] == "frame"]
            if (
                len(frames) != 1
                or len(frame_bounds) != 4
                or not all(math.isfinite(value) for value in frame_bounds)
                or min(frame_bounds[2:]) <= 0
            ):
                raise ValueError(f"Scene {sid} has invalid encapsulation geometry")
    if children != set(scenes) - {"root"}:
        raise ValueError("Semantic-zoom scene tree is disconnected")
    reachable, pending = set(), ["root"]
    while pending:
        sid = pending.pop()
        if sid in reachable:
            raise ValueError("Semantic-zoom scene tree contains a cycle")
        reachable.add(sid)
        pending.extend(node["childSceneId"] for node in scenes[sid]["nodes"] if node.get("childSceneId"))
    if reachable != set(scenes):
        raise ValueError("Semantic-zoom scene tree is disconnected")


def render_html(
    graph,
    output,
    *,
    layout="auto",
    timeout=120,
    color_arrows=True,
    svg_optimization="fast",
    external=False,
    initializers="auto",
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
    node_shape="bucketed",
    view_mode="standard",
):
    executable = shutil.which("d2")
    if not executable:
        raise ValueError("D2 is not installed. HTML export requires D2 0.9.0.")
    d2_version = _d2_version(executable)
    scenes = build_scenes(
        graph,
        external=external,
        initializers=initializers,
        exclude_arrows=exclude_arrows,
        exclude_arrows_to=exclude_arrows_to,
        exclude_arrows_from=exclude_arrows_from,
        view_mode=view_mode,
    )
    if view_mode == "isolated" and node_shape == "square":
        for scene in scenes.values():
            frame = next((node for node in scene.nodes if node.role == "frame"), None)
            if frame:
                frame.width = frame.height = 640
    order = sorted(
        scenes,
        key=lambda sid: ({"symbols": 0, "types": 1, "modules": 2}[scenes[sid].level], sid),
    )
    rendered = {}
    ratios = {}
    reports = []
    scene_store = tempfile.TemporaryDirectory(prefix="archer-scenes-")
    scene_directory = Path(scene_store.name)
    with tempfile.TemporaryDirectory(prefix="archer-html-") as temporary:
        directory = Path(temporary)
        for index, sid in enumerate(order, 1):
            scene = scenes[sid]
            if scene.level in {"types", "modules"}:
                assign_child_dimensions(scene, ratios, node_shape)
            print(f"Rendering scene {index}/{len(order)}: {scene.title}", file=sys.stderr)
            svg, viewbox, nodes, engine, warnings, stats = _render_scene(
                executable,
                scene,
                directory,
                layout,
                timeout,
                svg_optimization,
                color_arrows,
            )
            scene_path = scene_directory / f"{sid}.svg"
            scene_path.write_text(svg, encoding="utf-8")
            rendered[sid] = scene_path
            frame_id = scene.metadata.get("frameVisualId")
            frame = next((node for node in nodes if node["visualId"] == frame_id), None)
            ratios[sid] = frame["bounds"][2] / frame["bounds"][3] if frame else viewbox[2] / viewbox[3]
            edges = [
                {
                    "visualId": edge.visual_id,
                    "sourceVisualId": edge.source_visual_id,
                    "targetVisualId": edge.target_visual_id,
                    "kinds": sorted(edge.kinds),
                    "relationshipCount": edge.relationship_count,
                    "siteCount": edge.site_count,
                }
                for edge in scene.edges
            ]
            scene_manifest = {
                "level": scene.level,
                "ownerEntityId": scene.owner_entity_id,
                "parentSceneId": scene.parent_scene_id,
                "parentVisualId": scene.parent_visual_id,
                "title": scene.title,
                "viewBox": viewbox,
                "layout": engine,
                "entityCount": sum(n["entityId"] is not None and n["role"] == "node" for n in nodes),
                "nodes": nodes,
                "edges": edges,
                "diagnostics": warnings,
                "contextNodeCount": sum(n["role"] == "context" for n in nodes),
                "ownership": scene.metadata,
            }
            if frame:
                scene_manifest["frameBounds"] = frame["bounds"]
            reports.append((sid, scene_manifest, stats))

    scene_manifests = {sid: item for sid, item, _ in reports}
    for parent in scene_manifests.values():
        for node in parent["nodes"]:
            child_id = node.get("childSceneId")
            if not child_id:
                continue
            _, _, child_width, child_height = scene_manifests[child_id].get(
                "frameBounds", scene_manifests[child_id]["viewBox"]
            )
            _, _, card_width, card_height = node["bounds"]
            scale = min(card_width / child_width, card_height / child_height)
            scene_manifests[child_id]["letterboxOccupancy"] = (
                scale * scale * child_width * child_height / (card_width * card_height)
            )
    scene_manifests["root"]["letterboxOccupancy"] = 1.0
    manifest = {
        "schemaVersion": 1,
        "rootSceneId": "root",
        "build": {
            "archerVersion": __version__,
            "d2Version": d2_version,
            "nodeShape": node_shape,
            "svgOptimization": svg_optimization,
            "viewMode": view_mode,
            "incompleteScan": bool(graph.metadata.get("diagnostics")),
            "scanDiagnostics": graph.metadata.get("diagnostics", []),
        },
        "scenes": scene_manifests,
    }
    validate_manifest(manifest)
    css = files("archer.render.assets").joinpath("viewer.css").read_text(encoding="utf-8")
    js = files("archer.render.assets").joinpath("viewer.js").read_text(encoding="utf-8")
    header = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Archer architecture explorer</title><style>{css}</style></head>
<body><header><nav id="breadcrumbs" aria-label="Architecture breadcrumb"></nav><h1 id="scene-title">Architecture</h1>
<div class="controls"><button id="back" type="button">Back</button><button id="fit" type="button">Fit</button><button id="zoom-out" type="button" aria-label="Zoom out">−</button><button id="zoom-in" type="button" aria-label="Zoom in">+</button><button id="open" type="button">Open selected</button></div></header>
<main><div id="diagram" tabindex="0" aria-label="Architecture diagram"><div id="underlays" aria-hidden="true"></div><div id="stage" class="scene-layer"></div><div id="portal" class="scene-layer" aria-hidden="true"></div><div id="shell"></div><div id="status"></div></div>
<aside><h2>Current view</h2><p id="details">Select a node to see its full name.</p><ul id="node-list"></ul>
<details id="navigation-settings"><summary>Keyboard navigation</summary>
<label for="pan-speed">Arrow speed <output id="pan-speed-value" for="pan-speed">48 px</output></label>
<input id="pan-speed" type="range" min="8" max="160" step="4" value="48">
<label for="pan-zoom-scale">Speed per 2× zoom <output id="pan-zoom-scale-value" for="pan-zoom-scale">71%</output></label>
<input id="pan-zoom-scale" type="range" min="0.25" max="1" step="0.01" value="0.71">
<p id="pan-effective">Effective arrow step: 48 px</p><button id="pan-reset" type="button">Reset navigation</button>
</details>
<details id="semantic-zoom-settings"><summary>Semantic zoom</summary>
<label for="entry-threshold">Inner view appears at <output id="entry-threshold-value" for="entry-threshold">68%</output></label>
<input id="entry-threshold" type="range" min="40" max="120" step="1" value="68">
<label for="exit-threshold">Inner view disappears at <output id="exit-threshold-value" for="exit-threshold">62%</output></label>
<input id="exit-threshold" type="range" min="25" max="100" step="1" value="62">
<p class="setting-help">Percent of the viewport occupied on entry, and percent of fitted inner-view scale on exit.</p>
<button id="semantic-zoom-reset" type="button">Reset semantic zoom</button>
</details>
<details id="diagnostics" hidden><summary id="diagnostic-summary">Diagnostics</summary><ul id="diagnostic-list"></ul></details></aside></main>
<div id="live" class="sr-only" aria-live="polite"></div>
<script id="archer-manifest" type="application/json">{_json_data(manifest)}</script>
<script id="archer-scenes" type="application/json">{{"""
    trailer = f"""}}</script>
<script>{js}</script></body></html>"""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as artifact:
            artifact.write(header)
            for index, sid in enumerate(sorted(rendered)):
                if index:
                    artifact.write(",")
                artifact.write(_json_data(sid))
                artifact.write(":")
                artifact.write(_json_data(rendered[sid].read_text(encoding="utf-8")))
            artifact.write(trailer)
        os.replace(temporary, output)
    finally:
        scene_store.cleanup()
        if temporary.exists():
            temporary.unlink()
    size = output.stat().st_size
    if size > 50 * 1024 * 1024:
        print(f"Warning: HTML artifact is {size / 1024**2:.1f} MiB", file=sys.stderr)
    for sid, scene in manifest["scenes"].items():
        semantic_count = len(scene["nodes"]) + len(scene["edges"])
        if semantic_count > 2000:
            print(f"Warning: scene {sid} contains {semantic_count} semantic objects", file=sys.stderr)
    return {"output": str(output.resolve()), "scenes": len(scenes), "bytes": size, "layout": layout}
