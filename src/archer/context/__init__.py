from archer.graph.algorithms import metrics, project


def context(graph, max_chars=12000, level="modules"):
    if max_chars < 200:
        raise ValueError("max-chars must be at least 200")
    view = project(graph, level)
    info = metrics(graph)
    lines = [
        "# Archer architecture context",
        "",
        f"{info['modules']} modules; {info['dependencies']} internal dependencies.",
        "Resolution is static; unresolved calls are retained in the canonical IR.",
    ]
    diagnostics = graph.metadata.get("diagnostics", [])
    if diagnostics:
        lines.append(f"INCOMPLETE SCAN: {len(diagnostics)} diagnostics; inspect JSON diagnostics.")
    if graph.metadata.get("diff"):
        lines.append(f"Snapshots: {graph.metadata.get('before')} -> {graph.metadata.get('after')}")
    lines.extend(["", "## Hierarchy and source locations"])
    ordered = sorted(
        view.nodes.values(),
        key=lambda n: (
            n.metadata.get("change", "unchanged") == "unchanged",
            -(info["fan_in"].get(n.module, 0) + info["fan_out"].get(n.module, 0)),
            n.id,
        ),
    )
    for node in ordered:
        location = (
            f"{node.file}:{node.source_range['start']['line']}"
            if node.file and node.source_range
            else "namespace/external"
        )
        status = node.metadata.get("change", "")
        incoming = sorted({e.source for e in view.edges if e.target == node.id and e.kind != "contains"})
        outgoing = sorted({e.target for e in view.edges if e.source == node.id and e.kind != "contains"})
        lines.append(f"- {node.qualified_name} ({node.kind}; {location}) {status}".rstrip())
        if incoming:
            lines.append(
                "  Incoming: "
                + ", ".join(incoming[:12])
                + (f" (+{len(incoming) - 12} more)" if len(incoming) > 12 else "")
            )
        if outgoing:
            lines.append(
                "  Outgoing: "
                + ", ".join(outgoing[:12])
                + (f" (+{len(outgoing) - 12} more)" if len(outgoing) > 12 else "")
            )
    # Put changes and suspicious coupling before the potentially long inventory.
    priority = ["", "## Changes and architecture signals"]
    for component in info["components"]:
        priority.append("- Cycle/SCC: " + ", ".join(component))
    for module, count in sorted(info["fan_out"].items(), key=lambda item: (-item[1], item[0]))[:5]:
        if count:
            priority.append(f"- Coupling: {module}: fan-in {info['fan_in'][module]}, fan-out {count}")
    uncertain = sum(
        e.kind == "calls" and e.resolution["status"] in {"unresolved", "ambiguous"} for e in graph.edges
    )
    priority.append(f"- Unresolved/ambiguous call relationships: {uncertain}")
    for edge in graph.edges:
        if edge.metadata.get("change") in {"added", "removed", "modified"} and edge.kind != "contains":
            priority.append(f"- {edge.metadata['change']}: {edge.source} --{edge.kind}--> {edge.target}")
    output = "\n".join(lines[:4] + priority + lines[4:]) + "\n"
    if len(output) > max_chars:
        marker = "\n[Truncated; narrow with --focus or --radius, or raise --max-chars.]\n"
        output = output[: max_chars - len(marker)].rsplit("\n", 1)[0] + marker
    return output
