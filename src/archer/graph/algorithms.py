from copy import deepcopy

import networkx as nx

from archer.graph.model import Edge, Graph

RELATIONSHIPS = {"imports", "calls", "inherits"}
LEVELS = {"modules", "types", "symbols", "full", "changes"}


def dependencies(graph, modules=True):
    result = nx.DiGraph()
    for node in graph.nodes.values():
        if node.kind not in {"external", "unresolved"}:
            result.add_node(node.module if modules else node.id)
    for edge in graph.edges:
        if edge.kind not in RELATIONSHIPS:
            continue
        a, b = graph.nodes[edge.source], graph.nodes[edge.target]
        if a.kind in {"external", "unresolved"} or b.kind in {"external", "unresolved"}:
            continue
        source, target = (a.module, b.module) if modules else (a.id, b.id)
        if source != target or not modules:
            result.add_edge(source, target)
    return result


def neighborhood(graph, seeds, radius=2, direction="both"):
    if radius < 0:
        raise ValueError("radius must be nonnegative")
    net = dependencies(graph)
    if direction == "both":
        net = net.to_undirected()
    elif direction == "incoming":
        net = net.reverse()
    elif direction != "outgoing":
        raise ValueError("invalid traversal direction")
    keep = set(seeds)
    for seed in seeds:
        if seed in net:
            keep.update(nx.single_source_shortest_path_length(net, seed, cutoff=radius))
    ids = {n.id for n in graph.nodes.values() if n.module in keep}
    # Retain unresolved and external relationships attached to selected nodes.
    ids.update(
        e.target
        for e in graph.edges
        if e.source in ids and graph.nodes[e.target].kind in {"external", "unresolved"}
    )
    return subset(graph, ids)


def subset(graph, ids):
    return Graph(
        {k: deepcopy(n) for k, n in graph.nodes.items() if k in ids},
        [deepcopy(e) for e in graph.edges if e.source in ids and e.target in ids],
        deepcopy(graph.metadata),
    )


def changes(graph, radius=2, direction="both"):
    if not graph.metadata.get("diff"):
        raise ValueError("Change neighborhoods require a diff graph")
    seeds = {
        n.module
        for n in graph.nodes.values()
        if n.module and n.metadata.get("change") in {"added", "removed", "modified"}
    }
    for edge in graph.edges:
        if edge.metadata.get("change") in {"added", "removed", "modified"}:
            seeds.update(graph.nodes[x].module for x in (edge.source, edge.target) if graph.nodes[x].module)
    return neighborhood(graph, seeds, radius, direction)


def project(graph, level="modules", external=False):
    if level not in LEVELS:
        raise ValueError(f"Unknown view: {level}")
    if level in {"full", "changes"}:
        return subset(graph, set(graph.nodes))
    kinds = {"module", "package"} | ({"class"} if level == "types" else set())
    if level == "symbols":
        kinds |= {"class", "function", "method"}
    if external:
        kinds |= {"external", "unresolved"}
    nodes = {k: deepcopy(n) for k, n in graph.nodes.items() if n.kind in kinds}

    def owner(ident):
        if ident in nodes:
            return ident
        node = graph.nodes[ident]
        if node.kind in {"external", "unresolved"}:
            return None
        if level == "types":
            parent = ident.rpartition(".")[0]
            while parent:
                if parent in nodes:
                    return parent
                parent = parent.rpartition(".")[0]
        return node.module if node.module in nodes else None

    edges = {}
    for edge in graph.edges:
        source, target = owner(edge.source), owner(edge.target)
        if source is None or target is None or source == target:
            continue
        status = edge.metadata.get("change", "unchanged")
        # Preserve both added and removed relationships when aggregating symbols.
        key = source, target, edge.kind, status
        if key not in edges:
            projected = Edge(
                source,
                target,
                edge.kind,
                edge.source_range,
                deepcopy(edge.resolution),
                deepcopy(edge.metadata),
            )
            projected.metadata["relationship_count"] = 1
            edges[key] = projected
        else:
            edges[key].metadata["relationship_count"] += 1
            if edge.resolution["confidence"] < edges[key].resolution["confidence"]:
                edges[key].resolution = deepcopy(edge.resolution)
    return Graph(nodes, list(edges.values()), {**deepcopy(graph.metadata), "view": level})


def metrics(graph):
    net = dependencies(graph)
    components = sorted([sorted(c) for c in nx.strongly_connected_components(net) if len(c) > 1])
    return {
        "modules": net.number_of_nodes(),
        "dependencies": net.number_of_edges(),
        "components": components,
        "fan_in": dict(sorted(net.in_degree())),
        "fan_out": dict(sorted(net.out_degree())),
    }


def reachable(graph, source):
    net = dependencies(graph)
    return sorted(nx.descendants(net, source)) if source in net else []
