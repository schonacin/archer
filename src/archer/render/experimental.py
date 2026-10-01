"""Opt-in D3 map: canonical ownership + D2 geometry, no embedded D2 scenes.

The classic exporter and its assets are deliberately untouched. Each ownership
neighborhood is laid out independently, then embedded in its parent's card by
the browser. TALA is an export-time dependency, never a browser dependency.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from importlib.resources import files
from pathlib import Path

from archer.render import PALETTE, _excluded_arrow, hidden_initializers, subsystem
from archer.render.html import _d2_version, _json_data, _render_scene
from archer.render.scenes import Scene, SceneEdge, SceneNode, ownership, token
from archer.render.svg import path_boxes
from archer.render.svg_scene import SVG, _compose, _transform

ROOT = "@root"
KINDS = {"package", "module", "class", "function", "method"}


def build_map(
    graph,
    *,
    initializers="auto",
    external=False,
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
):
    """Keep arbitrary-depth ownership and full relationship provenance."""
    if graph.metadata.get("diff"):
        raise ValueError("Experimental HTML does not support diff graphs")
    direct, module_owner, _ = ownership(graph)
    hidden = hidden_initializers(graph, "modules", initializers)
    included = {
        ident
        for ident, node in graph.nodes.items()
        if node.kind in KINDS
        and (node.file or node.kind == "package")
        and (ident not in hidden or node.kind == "package")
    }
    if external:
        included.update(ident for ident, node in graph.nodes.items() if node.kind not in KINDS)
    for ident in sorted(hidden, key=lambda i: -len(i)):
        if (
            ident in included
            and graph.nodes[ident].kind == "package"
            and not any(direct.get(child) == ident for child in included)
        ):
            included.remove(ident)
    if ROOT in included:
        raise ValueError("Graph entity conflicts with experimental root ID")
    children = defaultdict(list)
    entities = {
        ROOT: {
            "id": ROOT,
            "name": "Architecture",
            "label": "Architecture",
            "kind": "root",
            "parent": None,
            "file": None,
            "line": None,
            "children": [],
        }
    }
    families = sorted({subsystem(graph.nodes[i].module or i) for i in included})
    colors = {name: PALETTE[index % len(PALETTE)][2] for index, name in enumerate(families)}
    for ident in sorted(included):
        node = graph.nodes[ident]
        parent = direct.get(ident)
        if parent not in included:
            # Source-backed older graphs can omit containment edges.
            parent = module_owner.get(ident)
            if parent == ident or parent not in included:
                parent = ROOT
        children[parent].append(ident)
        prefix = graph.nodes[parent].qualified_name + "." if parent in graph.nodes else ""
        label = node.qualified_name.removeprefix(prefix)
        point = (node.source_range or {}).get("start", {})
        entities[ident] = {
            "id": ident,
            "name": node.qualified_name,
            "label": label,
            "kind": node.kind,
            "parent": parent,
            "file": node.file,
            "line": point.get("line"),
            "color": colors[subsystem(node.module or ident)],
            "children": [],
        }
    for ident, entity in entities.items():
        entity["children"] = sorted(children[ident])
    # A package initializer is a module in its own right. Keep its declarations
    # together when the package also owns submodules, rather than mixing levels.
    initializer_by_package = {}
    for ident, entity in list(entities.items()):
        if entity["kind"] != "package":
            continue
        members = [i for i in entity["children"] if entities[i]["kind"] not in {"package", "module"}]
        if not members or len(members) == len(entity["children"]):
            continue
        initializer = "@initializer:" + ident
        if initializer in entities:
            raise ValueError("Graph entity conflicts with initializer region ID")
        entities[initializer] = {
            **entity,
            "id": initializer,
            "name": entity["name"] + ".__init__",
            "label": "__init__",
            "kind": "module",
            "parent": ident,
            "children": members,
        }
        initializer_by_package[ident] = initializer
        entity["children"] = sorted([i for i in entity["children"] if i not in members] + [initializer])
        for member in members:
            entities[member]["parent"] = initializer
    # Validate the hierarchy before recursion, including disconnected cycles.
    for ident in included:
        current, seen = ident, set()
        while current != ROOT:
            if current in seen:
                raise ValueError(f"Containment cycle involving {ident!r}")
            seen.add(current)
            current = entities[current]["parent"]
    relationships = []
    for edge in graph.edges:
        if edge.kind == "contains" or edge.source not in included or edge.target not in included:
            continue
        if _excluded_arrow(graph, edge, exclude_arrows, exclude_arrows_to, exclude_arrows_from):
            continue
        relationships.append(
            {
                "source": initializer_by_package.get(edge.source, edge.source),
                "target": initializer_by_package.get(edge.target, edge.target),
                "canonicalSource": edge.source,
                "canonicalTarget": edge.target,
                "kind": edge.kind,
                "count": max(1, len(edge.metadata.get("sites", []))),
            }
        )
    return {
        "version": 1,
        "root": ROOT,
        "entities": entities,
        "relationships": relationships,
        "layouts": {},
        "diagnostics": list(graph.metadata.get("diagnostics", [])),
    }


def _routes(svg):
    """Extract only connector geometry, normalized to the adapter's viewBox.

    Labels, masks, CSS and duplicated clipping paths are intentionally discarded.
    Fresh JS graphics use these routes and their own arrowheads.
    """
    root = ET.fromstring(svg)
    parents = {child: parent for parent in root.iter() for child in parent}
    nested = next((x for x in root if x.tag == f"{{{SVG}}}svg"), None)
    base = (1.0, 0.0, 0.0)
    if nested is not None:
        vb = [float(x) for x in nested.get("viewBox").split()]
        scale = min(float(nested.get("width")) / vb[2], float(nested.get("height")) / vb[3])
        base = (scale, float(nested.get("x", 0)) - scale * vb[0], float(nested.get("y", 0)) - scale * vb[1])
    result = {}
    for group in root.iter():
        if group.get("data-role") != "edge":
            continue
        paths, seen = [], set()
        for path in group.iter(f"{{{SVG}}}path"):
            d = path.get("d")
            if not d or "connection" not in path.get("class", "").split():
                continue
            pose, current = (1.0, 0.0, 0.0), path
            while current is not None and current is not nested:
                pose = _compose(_transform(current.get("transform")), pose)
                current = parents.get(current)
            if nested is not None:
                pose = _compose(_transform(nested.get("transform")), pose)
            pose = _compose(base, pose)
            key = (d, pose)
            if key not in seen:
                paths.append({"d": d, "transform": list(pose)})
                seen.add(key)
        if not paths:
            raise ValueError(f"Connector {group.get('data-visual-id')} has no supported route")
        result[group.get("data-visual-id")] = paths
    return result


def layout_map(data, executable, directory, *, layout="auto", timeout=120, color_arrows=True):
    entities = data["entities"]
    owners = [i for i, e in entities.items() if e["children"]]

    def depth(ident):
        count = 0
        while entities[ident]["parent"] is not None:
            count += 1
            ident = entities[ident]["parent"]
        return count

    owners.sort(key=lambda i: (-depth(i), i))
    for index, ident in enumerate(owners, 1):
        entity = entities[ident]
        scene = Scene(token("map", ident), "modules", ident, None, None, entity["name"])
        visual = {}
        for child in entity["children"]:
            child_layout = data["layouts"].get(child)
            if child_layout:
                w, h = child_layout["bounds"][2:]
                ratio = max(0.3, min(6, w / h))
                width = 440
                height = max(180, round((width - 32) / ratio) + 72)
            else:
                width, height = 280, 100
            vid = token("n", child)
            visual[child] = vid
            scene.nodes.append(
                SceneNode(
                    vid, child, entities[child]["label"], entities[child]["kind"], width=width, height=height
                )
            )

        def representative(endpoint, owner=ident):
            while endpoint != ROOT:
                if entities[endpoint]["parent"] == owner:
                    return endpoint
                endpoint = entities[endpoint]["parent"]
            return None

        aggregates = {}
        for edge in data["relationships"]:
            a, b = representative(edge["source"]), representative(edge["target"])
            if a and b and a != b:
                key = (a, b)
                item = aggregates.setdefault(key, {"kinds": set(), "count": 0})
                item["kinds"].add(edge["kind"])
                item["count"] += edge["count"]
        for (a, b), item in sorted(aggregates.items()):
            scene.edges.append(
                SceneEdge(
                    token("e", ident + "\0" + a + "\0" + b),
                    visual[a],
                    visual[b],
                    item["kinds"],
                    site_count=item["count"],
                    color=entities[a]["color"],
                )
            )
        print(f"Laying out map {index}/{len(owners)}: {entity['name']}", file=sys.stderr)
        svg, _, nodes, engine, warnings, _ = _render_scene(
            executable,
            scene,
            directory,
            layout,
            timeout,
            "raw",
            color_arrows,
        )
        bounds = [n["bounds"] for n in nodes]
        x, y = min(b[0] for b in bounds), min(b[1] for b in bounds)
        right, bottom = max(b[0] + b[2] for b in bounds), max(b[1] + b[3] for b in bounds)
        # Include TALA's detours so camera fitting does not crop connectors.
        # Empty edge-free scenes still retain a modest, predictable margin.
        routes = _routes(svg)
        compact = entity["kind"] in {"package", "root"} and len(nodes) > 2
        if compact:
            # TALA determines reference ordering; packages need readable regions
            # more than a long directed flow. Repack cards without changing order
            # on zoom, and regenerate routes against their new boundaries.
            order = sorted(nodes, key=lambda n: (n["bounds"][1], n["bounds"][0], n["entityId"]))
            cols = math.ceil(math.sqrt(len(order)))
            cell_w, cell_h = 440, 280
            for i, node in enumerate(order):
                node["bounds"] = [
                    32 + (i % cols) * (cell_w + 52),
                    32 + (i // cols) * (cell_h + 42),
                    cell_w,
                    cell_h,
                ]
            by_visual = {n["visualId"]: n["bounds"] for n in nodes}
            for edge in scene.edges:
                a, b = by_visual[edge.source_visual_id], by_visual[edge.target_visual_id]
                if a[0] == b[0]:
                    sx, sy = a[0] + a[2] / 2, a[1] + (a[3] if b[1] > a[1] else 0)
                    tx, ty = b[0] + b[2] / 2, b[1] + (0 if b[1] > a[1] else b[3])
                    path = f"M {sx} {sy} C {sx} {(sy + ty) / 2} {tx} {(sy + ty) / 2} {tx} {ty}"
                else:
                    sx, sy = a[0] + (a[2] if b[0] > a[0] else 0), a[1] + a[3] / 2
                    tx, ty = b[0] + (0 if b[0] > a[0] else b[2]), b[1] + b[3] / 2
                    path = f"M {sx} {sy} C {(sx + tx) / 2} {sy} {(sx + tx) / 2} {ty} {tx} {ty}"
                routes[edge.visual_id] = [{"d": path, "transform": [1, 0, 0]}]
            bounds = [n["bounds"] for n in nodes]
            x, y = min(b[0] for b in bounds), min(b[1] for b in bounds)
            right, bottom = max(b[0] + b[2] for b in bounds), max(b[1] + b[3] for b in bounds)
        for paths in routes.values():
            for path in paths:
                for p in path_boxes(path["d"], 0):
                    s, tx, ty = path["transform"]
                    x, y = min(x, p[0] * s + tx), min(y, p[1] * s + ty)
                    right, bottom = max(right, p[2] * s + tx), max(bottom, p[3] * s + ty)
        data["layouts"][ident] = {
            "bounds": [x - 24, y - 24, right - x + 48, bottom - y + 48],
            "engine": engine,
            "spacing": "compact" if compact else "reference",
            "nodes": {n["entityId"]: n["bounds"] for n in nodes},
            "edges": [
                {
                    "id": e.visual_id,
                    "source": next(k for k, v in visual.items() if v == e.source_visual_id),
                    "target": next(k for k, v in visual.items() if v == e.target_visual_id),
                    "kinds": sorted(e.kinds),
                    "count": e.site_count,
                    "paths": routes[e.visual_id],
                }
                for e in scene.edges
            ],
        }
        data["diagnostics"].extend(warnings)
    if not owners:
        data["layouts"][ROOT] = {"bounds": [0, 0, 640, 400], "engine": "none", "nodes": {}, "edges": []}
    return data


def render_html(
    graph,
    output,
    *,
    layout="auto",
    timeout=120,
    color_arrows=True,
    external=False,
    initializers="auto",
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
):
    executable = shutil.which("d2")
    if not executable:
        raise ValueError("Experimental HTML requires D2 0.9.0 for export-time layout")
    version = _d2_version(executable)
    data = build_map(
        graph,
        initializers=initializers,
        external=external,
        exclude_arrows=exclude_arrows,
        exclude_arrows_to=exclude_arrows_to,
        exclude_arrows_from=exclude_arrows_from,
    )
    with tempfile.TemporaryDirectory(prefix="archer-map-") as temporary:
        layout_map(
            data, executable, Path(temporary), layout=layout, timeout=timeout, color_arrows=color_arrows
        )
    data["build"] = {"d2Version": version, "colorArrows": color_arrows, "layout": layout}
    assets = files("archer.render.assets").joinpath("experimental")
    page = assets.joinpath("viewer.html").read_text(encoding="utf-8")
    replacements = {
        "STYLE": assets.joinpath("viewer.css").read_text(encoding="utf-8"),
        "D3": assets.joinpath("d3.min.js").read_text(encoding="utf-8"),
        "LICENSE": assets.joinpath("D3-LICENSE").read_text(encoding="utf-8"),
        "DATA": _json_data(data),
        "SCRIPT": assets.joinpath("viewer.js").read_text(encoding="utf-8"),
    }
    page = re.sub(r"\{\{(STYLE|D3|LICENSE|DATA|SCRIPT)\}\}", lambda match: replacements[match.group(1)], page)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(page, encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "output": str(output.resolve()),
        "viewer": "experimental",
        "layouts": len(data["layouts"]),
        "entities": len(data["entities"]) - 1,
        "bytes": output.stat().st_size,
        "layout": layout,
    }
