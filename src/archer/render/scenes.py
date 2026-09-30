"""Deterministic semantic-zoom scene preparation.

This module deliberately contains no renderer or browser logic.  It turns one
already-scoped canonical graph into the three-level tree consumed by the HTML
exporter.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from archer.graph.model import Edge, Graph, Node, resolution
from archer.render import _excluded_arrow, hidden_initializers, subsystem, subsystem_styles


def token(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def scene_id(level: str, owner: str | None) -> str:
    return "root" if owner is None else token("s", f"{level}\0{owner}")


@dataclass
class SceneNode:
    visual_id: str
    entity_id: str | None
    label: str
    kind: str
    child_scene_id: str | None = None
    width: int | None = None
    height: int | None = None
    role: str = "node"
    metadata: dict = field(default_factory=dict)


@dataclass
class SceneEdge:
    visual_id: str
    source_visual_id: str
    target_visual_id: str
    kinds: set[str]
    relationship_count: int = 1
    site_count: int = 0
    dashed: bool = False
    provenance: list[tuple[str, str, str]] = field(default_factory=list)
    color: str | None = None


@dataclass
class Scene:
    scene_id: str
    level: str
    owner_entity_id: str | None
    parent_scene_id: str | None
    parent_visual_id: str | None
    title: str
    nodes: list[SceneNode] = field(default_factory=list)
    edges: list[SceneEdge] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def ownership(graph: Graph):
    """Return canonical direct owners and validated nearest module/class indexes."""
    direct: dict[str, str] = {}
    for edge in graph.edges:
        if edge.kind != "contains":
            continue
        previous = direct.get(edge.target)
        if previous is not None and previous != edge.source:
            raise ValueError(
                f"Ambiguous containment for {edge.target!r}: owned by {previous!r} and {edge.source!r}"
            )
        direct[edge.target] = edge.source

    def nearest(ident: str, kinds: set[str]) -> str | None:
        seen = set()
        current = ident
        while current in graph.nodes and current not in seen:
            seen.add(current)
            if graph.nodes[current].kind in kinds:
                return current
            current = direct.get(current, "")
        if current in seen:
            raise ValueError(f"Containment cycle involving {ident!r}")
        # Older saved graphs may omit module containment edges.  The canonical
        # node.module field is an established ownership field, not name parsing.
        module = graph.nodes[ident].module
        return module if module in graph.nodes and graph.nodes[module].kind in kinds else None

    modules = {key: nearest(key, {"module", "package"}) for key in graph.nodes}
    classes = {key: nearest(key, {"class"}) for key in graph.nodes}
    return direct, modules, classes


def _site_count(edge: Edge) -> int:
    sites = edge.metadata.get("sites")
    return len(sites) if isinstance(sites, list) else int(edge.source_range is not None)


def _add_edges(scene: Scene, records, graph: Graph, colors):
    grouped = {}
    for source, target, edge in records:
        if source == target or source is None or target is None:
            continue
        key = source, target
        item = grouped.get(key)
        if item is None:
            item = SceneEdge(token("e", f"{scene.scene_id}\0{source}\0{target}"), source, target, set())
            family = colors.get(subsystem(graph.nodes[edge.source].module))
            item.color = family[2] if family else "#64748b"
            grouped[key] = item
        item.kinds.add(edge.kind)
        item.relationship_count += 0 if len(item.provenance) == 0 else 1
        item.site_count += _site_count(edge)
        item.dashed |= edge.resolution["status"] in {"unresolved", "ambiguous", "inferred"}
        item.provenance.append(edge.key)
    scene.edges = [grouped[key] for key in sorted(grouped)]


def _relative_class_label(graph: Graph, module: str, ident: str) -> str:
    qualified = graph.nodes[ident].qualified_name
    prefix = graph.nodes[module].qualified_name + "."
    return qualified.removeprefix(prefix)


def _context(scene: Scene, label: str, key: str, kind="context") -> SceneNode:
    visual = token("x", f"{scene.scene_id}\0{key}")
    node = next((n for n in scene.nodes if n.visual_id == visual), None)
    if node is None:
        node = SceneNode(visual, None, f"Outside this view: {label}", kind, role="context")
        scene.nodes.append(node)
    return node


def _dimensions(ratio: float, shape: str):
    if shape == "square":
        ratio = 1.0
    else:
        ratio = min(2.0, max(0.75, ratio))
        buckets = (0.75, 1.0, 1.5, 2.0)
        ratio = min(buckets, key=lambda x: (abs(math.log(ratio / x)), x != 1.0, x))
    return math.ceil(math.sqrt(40000 * ratio)), math.ceil(math.sqrt(40000 / ratio))


def assign_child_dimensions(scene: Scene, ratios: dict[str, float], shape: str):
    for node in scene.nodes:
        if node.child_scene_id:
            node.width, node.height = _dimensions(ratios[node.child_scene_id], shape)


def build_scenes(
    graph: Graph,
    *,
    external=False,
    initializers="auto",
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
    view_mode="standard",
):
    if view_mode not in {"standard", "isolated"}:
        raise ValueError(f"Unknown HTML view mode: {view_mode}")
    if graph.metadata.get("diff"):
        raise ValueError("HTML semantic zoom does not support diff graphs")
    direct, module_owner, class_owner = ownership(graph)
    colors = subsystem_styles(graph)
    hidden = hidden_initializers(graph, "modules", initializers)
    source_modules = [
        key
        for key, node in graph.nodes.items()
        if node.kind in {"module", "package"} and node.file and key not in hidden
    ]
    source_modules.sort()
    classes = sorted(
        key
        for key, node in graph.nodes.items()
        if node.kind == "class" and module_owner[key] in source_modules
    )

    root = Scene("root", "modules", None, None, None, "Modules")
    module_visual = {}
    packages = sorted(key for key, node in graph.nodes.items() if node.kind == "package")
    group_visuals = {ident: token("g", f"root\0{ident}") for ident in packages}
    for ident in packages:
        ancestors = [package for package in packages if ident != package and ident.startswith(package + ".")]
        parent = max(ancestors, key=len) if ancestors else None
        family = colors.get(subsystem(graph.nodes[ident].module or ident))
        root.nodes.append(
            SceneNode(
                group_visuals[ident],
                ident,
                graph.nodes[ident].qualified_name,
                "package",
                role="group",
                metadata={
                    "parentVisualId": group_visuals[parent] if parent else None,
                    "fill": family[0] if family else "#f8fafc",
                    "stroke": family[2] if family else "#64748b",
                },
            )
        )
    for ident in source_modules:
        visual = token("n", f"root\0{ident}")
        module_visual[ident] = visual
        node = graph.nodes[ident]
        if node.kind == "package":
            parent, display = ident, "__init__.py"
        else:
            ancestors = [package for package in packages if ident.startswith(package + ".")]
            parent = max(ancestors, key=len) if ancestors else None
            display = ident.rsplit(".", 1)[-1]
        family = colors.get(subsystem(node.module or ident))
        root.nodes.append(
            SceneNode(
                visual,
                ident,
                node.qualified_name,
                node.kind,
                scene_id("types", ident),
                metadata={
                    "parentVisualId": group_visuals[parent] if parent else None,
                    "displayLabel": display,
                    "fill": family[1] if family else "#f1f5f9",
                    "stroke": family[2] if family else "#64748b",
                },
            )
        )
    # Aggregate every canonical relationship at module ownership.
    records = []
    for edge in graph.edges:
        if edge.kind == "contains" or _excluded_arrow(
            graph, edge, exclude_arrows, exclude_arrows_to, exclude_arrows_from
        ):
            continue
        a, b = module_owner.get(edge.source), module_owner.get(edge.target)
        if a in module_visual and b in module_visual:
            records.append((module_visual[a], module_visual[b], edge))
    _add_edges(root, records, graph, colors)
    root.metadata = {"hiddenInitializerCount": len(hidden & graph.nodes.keys())}

    scenes = {"root": root}
    class_visual_by_module = {}
    for module in source_modules:
        sid = scene_id("types", module)
        scene = Scene(
            sid,
            "types",
            module,
            "root",
            module_visual[module],
            f"Types in {graph.nodes[module].qualified_name}",
        )
        owned = [key for key in classes if module_owner[key] == module]
        visuals = {}
        for ident in owned:
            visual = token("n", f"{sid}\0{ident}")
            visuals[ident] = visual
            family = colors.get(subsystem(graph.nodes[ident].module))
            scene.nodes.append(
                SceneNode(
                    visual,
                    ident,
                    _relative_class_label(graph, module, ident),
                    "class",
                    scene_id("symbols", ident),
                    metadata={
                        "fill": family[1] if family else "#f1f5f9",
                        "stroke": family[2] if family else "#64748b",
                    },
                )
            )
        class_visual_by_module[module] = visuals
        free_members = [
            key
            for key, node in graph.nodes.items()
            if node.kind == "function" and module_owner[key] == module and class_owner[key] is None
        ]
        top_level_free = [key for key in free_members if direct.get(key) == module]
        scene.metadata["freeFunctionCount"] = len(top_level_free)
        records = []
        for edge in graph.edges:
            if edge.kind == "contains" or _excluded_arrow(
                graph, edge, exclude_arrows, exclude_arrows_to, exclude_arrows_from
            ):
                continue
            if not any(
                class_owner.get(endpoint) in visuals
                or (module_owner.get(endpoint) == module and endpoint in free_members)
                for endpoint in (edge.source, edge.target)
            ):
                continue
            endpoint_visuals = []
            for endpoint in (edge.source, edge.target):
                owner_class = class_owner.get(endpoint)
                owner_module = module_owner.get(endpoint)
                if owner_class in visuals:
                    endpoint_visuals.append(visuals[owner_class])
                elif owner_module == module and endpoint in free_members:
                    # Free functions remain part of aggregate relationships but
                    # are truthfully represented as module context.
                    endpoint_visuals.append(
                        _context(scene, "module-level functions", f"free:{module}").visual_id
                    )
                elif graph.nodes[endpoint].kind in {"external", "unresolved"}:
                    if not external:
                        endpoint_visuals.append(None)
                    else:
                        endpoint_visuals.append(
                            _context(scene, graph.nodes[endpoint].qualified_name, endpoint).visual_id
                        )
                else:
                    label = (
                        graph.nodes[owner_class].qualified_name
                        if owner_class
                        else graph.nodes[owner_module].qualified_name
                        if owner_module in graph.nodes
                        else graph.nodes[endpoint].qualified_name
                    )
                    endpoint_visuals.append(
                        _context(scene, label, owner_class or owner_module or endpoint).visual_id
                    )
            records.append((*endpoint_visuals, edge))
        _add_edges(scene, records, graph, colors)
        scenes[sid] = scene

    for owner in classes:
        module = module_owner[owner]
        sid = scene_id("symbols", owner)
        parent = scene_id("types", module)
        scene = Scene(
            sid,
            "symbols",
            owner,
            parent,
            class_visual_by_module[module][owner],
            f"Members of {graph.nodes[owner].qualified_name}",
        )
        members = sorted(
            key
            for key, node in graph.nodes.items()
            if node.kind in {"method", "function"} and class_owner[key] == owner
        )
        visuals = {}
        for ident in members:
            visual = token("n", f"{sid}\0{ident}")
            visuals[ident] = visual
            label = graph.nodes[ident].qualified_name.removeprefix(graph.nodes[owner].qualified_name + ".")
            family = colors.get(subsystem(graph.nodes[ident].module))
            scene.nodes.append(
                SceneNode(
                    visual,
                    ident,
                    label,
                    graph.nodes[ident].kind,
                    metadata={
                        "fill": family[1] if family else "#f1f5f9",
                        "stroke": family[2] if family else "#64748b",
                    },
                )
            )
        owner_visual = token("o", f"{sid}\0{owner}")
        scene.nodes.append(
            SceneNode(owner_visual, owner, graph.nodes[owner].qualified_name, "class", role="owner")
        )

        def inside(candidate, ancestor):
            seen = set()
            current = direct.get(candidate)
            while current and current not in seen:
                if current == ancestor:
                    return True
                seen.add(current)
                current = direct.get(current)
            return False

        nested = [key for key in classes if key != owner and inside(key, owner)]
        scene.metadata["nestedClassCount"] = len(nested)
        if not members:
            scene.nodes.append(
                SceneNode(
                    token("z", f"{sid}\0empty"),
                    None,
                    "No members declared in this class",
                    "context",
                    role="empty",
                )
            )
        records = []
        for edge in graph.edges:
            if edge.kind == "contains" or _excluded_arrow(
                graph, edge, exclude_arrows, exclude_arrows_to, exclude_arrows_from
            ):
                continue
            if not any(endpoint in visuals or endpoint == owner for endpoint in (edge.source, edge.target)):
                continue
            if not external and any(
                graph.nodes[endpoint].kind in {"external", "unresolved"}
                for endpoint in (edge.source, edge.target)
            ):
                continue
            endpoint_visuals = []
            for endpoint in (edge.source, edge.target):
                if endpoint in visuals:
                    endpoint_visuals.append(visuals[endpoint])
                elif endpoint == owner:
                    endpoint_visuals.append(owner_visual)
                else:
                    other_class, other_module = class_owner.get(endpoint), module_owner.get(endpoint)
                    label = (
                        graph.nodes[other_class].qualified_name
                        if other_class
                        else graph.nodes[other_module].qualified_name
                        if other_module in graph.nodes
                        else graph.nodes[endpoint].qualified_name
                    )
                    endpoint_visuals.append(
                        _context(scene, label, other_class or other_module or endpoint).visual_id
                    )
            records.append((*endpoint_visuals, edge))
        _add_edges(scene, records, graph, colors)
        scenes[sid] = scene

    if view_mode == "isolated":
        scenes = _isolated_views(
            graph,
            scenes,
            source_modules,
            classes,
            direct,
            module_owner,
            class_owner,
            colors,
            exclude_arrows,
            exclude_arrows_to,
            exclude_arrows_from,
        )
        return scenes

    expected = 1 + len(source_modules) + len(classes)
    if len(scenes) != expected:
        raise ValueError(f"Invalid scene inventory: expected {expected}, built {len(scenes)}")
    return scenes


def _isolated_views(
    graph,
    scenes,
    source_modules,
    classes,
    direct,
    module_owner,
    class_owner,
    colors,
    exclude_arrows,
    exclude_arrows_to,
    exclude_arrows_from,
):
    """Convert detail scenes to local-only, owner-framed views."""

    def excluded(edge):
        return edge.kind == "contains" or _excluded_arrow(
            graph, edge, exclude_arrows, exclude_arrows_to, exclude_arrows_from
        )

    def frame(scene, owner, *, rounded):
        family = colors.get(subsystem(graph.nodes[owner].module or owner))
        visual = token("f", f"{scene.scene_id}\0{owner}")
        scene.nodes.insert(
            0,
            SceneNode(
                visual,
                owner,
                graph.nodes[owner].qualified_name,
                graph.nodes[owner].kind,
                role="frame",
                metadata={
                    "fill": family[1] if family else "#f1f5f9",
                    "stroke": family[2] if family else "#64748b",
                    "borderRadius": 12 if rounded else 0,
                },
            ),
        )
        for node in scene.nodes[1:]:
            node.metadata["parentVisualId"] = visual
        scene.metadata["frameVisualId"] = visual
        return visual

    def free_functions(module):
        return sorted(
            key
            for key, node in graph.nodes.items()
            if node.kind == "function" and module_owner[key] == module and class_owner[key] is None
        )

    def function_node(scene, module, ident):
        family = colors.get(subsystem(graph.nodes[ident].module))
        prefix = graph.nodes[module].qualified_name + "."
        return SceneNode(
            token("n", f"{scene.scene_id}\0{ident}"),
            ident,
            graph.nodes[ident].qualified_name.removeprefix(prefix),
            "function",
            metadata={
                "fill": family[1] if family else "#f1f5f9",
                "stroke": family[2] if family else "#64748b",
            },
        )

    root = scenes["root"]
    root_by_entity = {node.entity_id: node for node in root.nodes}
    classes_by_module = {
        module: [owner for owner in classes if module_owner[owner] == module] for module in source_modules
    }

    for module in source_modules:
        owned_classes = classes_by_module[module]
        functions = free_functions(module)
        if not owned_classes:
            old_sid = scene_id("types", module)
            scenes.pop(old_sid, None)
            sid = scene_id("symbols", module)
            scene = Scene(
                sid,
                "symbols",
                module,
                "root",
                root_by_entity[module].visual_id,
                f"Symbols in {graph.nodes[module].qualified_name}",
                metadata={"freeFunctionCount": len(functions), "nestedClassCount": 0},
            )
            root_by_entity[module].child_scene_id = sid
            scene.nodes.extend(function_node(scene, module, ident) for ident in functions)
            if not functions:
                scene.nodes.append(
                    SceneNode(
                        token("z", f"{sid}\0empty"),
                        None,
                        "No symbols declared in this module",
                        "context",
                        role="empty",
                    )
                )
            owner_visual = frame(scene, module, rounded=False)
            visuals = {node.entity_id: node.visual_id for node in scene.nodes if node.entity_id}
            records = []
            for edge in graph.edges:
                if excluded(edge):
                    continue
                source = owner_visual if edge.source == module else visuals.get(edge.source)
                target = owner_visual if edge.target == module else visuals.get(edge.target)
                if source and target:
                    records.append((source, target, edge))
            _add_edges(scene, records, graph, colors)
            scenes[sid] = scene
            continue

        scene = scenes[scene_id("types", module)]
        scene.nodes = [node for node in scene.nodes if node.role == "node"]
        scene.nodes.extend(function_node(scene, module, ident) for ident in functions)
        owner_visual = frame(scene, module, rounded=False)
        class_visuals = {node.entity_id: node.visual_id for node in scene.nodes if node.kind == "class"}
        function_visuals = {node.entity_id: node.visual_id for node in scene.nodes if node.kind == "function"}

        def endpoint(
            ident,
            module=module,
            owner_visual=owner_visual,
            class_visuals=class_visuals,
            function_visuals=function_visuals,
        ):
            if ident == module:
                return owner_visual
            owner = class_owner.get(ident)
            if owner in class_visuals:
                return class_visuals[owner]
            return function_visuals.get(ident)

        records = []
        for edge in graph.edges:
            if excluded(edge):
                continue
            source, target = endpoint(edge.source), endpoint(edge.target)
            if source and target:
                records.append((source, target, edge))
        _add_edges(scene, records, graph, colors)

    for owner in classes:
        scene = scenes[scene_id("symbols", owner)]
        scene.nodes = [node for node in scene.nodes if node.role == "node"]
        if not scene.nodes:
            scene.nodes.append(
                SceneNode(
                    token("z", f"{scene.scene_id}\0empty"),
                    None,
                    "No members declared in this class",
                    "context",
                    role="empty",
                )
            )
        owner_visual = frame(scene, owner, rounded=True)
        visuals = {node.entity_id: node.visual_id for node in scene.nodes if node.entity_id}
        records = []
        for edge in graph.edges:
            if excluded(edge):
                continue
            source = owner_visual if edge.source == owner else visuals.get(edge.source)
            target = owner_visual if edge.target == owner else visuals.get(edge.target)
            if source and target:
                records.append((source, target, edge))
        _add_edges(scene, records, graph, colors)

    return scenes


def scene_graph(scene: Scene) -> Graph:
    """A lightweight graph for density policy and diagnostics."""
    nodes = {
        node.visual_id: Node(
            node.visual_id,
            node.kind if node.kind != "context" else "external",
            node.label,
            "",
            None,
            None,
        )
        for node in scene.nodes
    }
    edges = [
        Edge(
            edge.source_visual_id,
            edge.target_visual_id,
            next(iter(edge.kinds)),
            None,
            resolution(),
        )
        for edge in scene.edges
    ]
    return Graph(nodes, edges, {"view": scene.level})
