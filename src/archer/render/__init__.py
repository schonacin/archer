"""D2 is an optional external adapter. D2 source generation requires no binary."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

from archer.graph.algorithms import project
from archer.render.svg import optimize_svg

COLORS = {"added": "#dcfce7", "removed": "#fee2e2", "modified": "#fef3c7"}


# Light container / node fill / contrasting border. Assigned by subsystem name.
PALETTE = [
    ("#eff6ff", "#dbeafe", "#2563eb"),
    ("#faf5ff", "#f3e8ff", "#9333ea"),
    ("#ecfeff", "#cffafe", "#0891b2"),
    ("#fdf2f8", "#fce7f3", "#db2777"),
    ("#eef2ff", "#e0e7ff", "#4f46e5"),
    ("#fff7ed", "#ffedd5", "#c2410c"),
    ("#f0fdfa", "#ccfbf1", "#0f766e"),
    ("#f7fee7", "#ecfccb", "#4d7c0f"),
    ("#f0f9ff", "#e0f2fe", "#0369a1"),
    ("#fdf4ff", "#fae8ff", "#a21caf"),
]


def subsystem(name):
    return ".".join(name.split(".")[:2])


def subsystem_styles(view):
    # Scan metadata survives focusing/projection, keeping colors consistent.
    modules = view.metadata.get("modules", [n.module for n in view.nodes.values() if n.module])
    families = sorted({subsystem(name) for name in modules if "." in name})
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(families)}


def nested_nodes(view, level):
    """Build display containers, including missing ancestors in focused views.

    A package's initializer is an explicit leaf so imports never point from a
    container to its own descendant (unsupported by some D2 layouts).
    """
    styles = {} if view.metadata.get("diff") else subsystem_styles(view)
    groups = set()
    for node in view.nodes.values():
        if not node.module:
            continue
        parts = node.module.split(".")
        groups.update(".".join(parts[:i]) for i in range(1, len(parts)))
        if node.kind == "package" or node.id != node.module:
            groups.add(node.module)
    group_ids = {}
    for name in sorted(groups, key=lambda name: (name.count("."), name)):
        parent = name.rpartition(".")[0]
        local = "g" + hashlib.sha256(name.encode()).hexdigest()[:20]
        group_ids[name] = (group_ids[parent] + "." if parent in group_ids else "") + local
    ids, children, members = {}, {}, {}
    for name in groups:
        parent = name.rpartition(".")[0]
        children.setdefault(parent if parent in groups else "", []).append(name)
    for key, node in sorted(view.nodes.items()):
        parent = node.module if node.module in groups else node.module.rpartition(".")[0]
        if parent not in groups:
            parent = ""
        local = "n" + hashlib.sha256(key.encode()).hexdigest()[:20]
        ids[key] = (group_ids[parent] + "." if parent else "") + local
        members.setdefault(parent, []).append((key, local))
    lines = []

    def emit(parent, depth):
        indent = "  " * depth
        for name in sorted(children.get(parent, [])):
            ident = group_ids[name].rsplit(".", 1)[-1]
            label = name[len(parent) + 1 :] if parent else name
            node = view.nodes.get(name)
            color = COLORS.get(node.metadata.get("change")) if node else None
            family = styles.get(subsystem(name))
            background = family[0] if family else ("#f8fafc" if depth % 2 == 0 else "#ffffff")
            border = family[2] if family else "#94a3b8"
            lines.append(f"{indent}{ident}: {json.dumps(label)} {{")
            lines.append(f'{indent}  style.fill: "{color or background}"')
            lines.append(f'{indent}  style.stroke: "{border}"')
            if node and node.metadata.get("change") == "removed":
                lines.append(f"{indent}  style.stroke-dash: 4")
            emit(name, depth + 1)
            lines.append(indent + "}")
        for key, local in members.get(parent, []):
            node = view.nodes[key]
            if key == parent:
                label = "__init__.py" if node.kind == "package" and node.file else f"({node.kind})"
            else:
                prefix = parent + "." if parent else ""
                label = node.qualified_name.removeprefix(prefix)
                if level in {"full", "symbols", "types", "changes"}:
                    label += " [" + node.kind + "]"
            lines.append(f"{indent}{local}: {json.dumps(label)} {{")
            family = styles.get(subsystem(node.module))
            border = family[2] if family else "#64748b"
            lines.append(f'{indent}  style.stroke: "{border}"')
            lines.append(f'{indent}  style.font-color: "#0f172a"')
            color = COLORS.get(node.metadata.get("change"), family[1] if family else "#f1f5f9")
            lines.append(f'{indent}  style.fill: "{color}"')
            if node.metadata.get("change") == "removed":
                lines.append(f"{indent}  style.stroke-dash: 4")
            lines.append(indent + "}")

    emit("", 0)
    return ids, lines


def _matches_module(module, selections):
    return any(module == selected or module.startswith(selected + ".") for selected in selections)


def _excluded_arrow(view, edge, exclude_arrows, exclude_arrows_to, exclude_arrows_from):
    source = view.nodes[edge.source].module
    target = view.nodes[edge.target].module
    return _matches_module(source, (*exclude_arrows, *exclude_arrows_from)) or _matches_module(
        target, (*exclude_arrows, *exclude_arrows_to)
    )


def d2_source(
    graph,
    level="modules",
    external=False,
    color_arrows=False,
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
):
    view = project(graph, level, external)
    ids, declarations = nested_nodes(view, level)
    legend = (
        "green=added, red=removed, amber=modified"
        if view.metadata.get("diff")
        else "colors identify subsystems; nesting shows package/module hierarchy"
    )
    lines = ["direction: right", "# Archer IR projection; " + legend]
    lines.extend(declarations)
    arrow_styles = subsystem_styles(view) if color_arrows and not view.metadata.get("diff") else {}
    displayed = view.edges
    if level == "modules":
        grouped = {}
        for edge in view.edges:
            if edge.kind == "contains":
                continue
            key = edge.source, edge.target, edge.metadata.get("change", "")
            if key not in grouped:
                grouped[key] = deepcopy(edge)
                grouped[key].metadata["kinds"] = {edge.kind}
            else:
                grouped[key].metadata["kinds"].add(edge.kind)
                if edge.resolution["confidence"] < grouped[key].resolution["confidence"]:
                    grouped[key].resolution = deepcopy(edge.resolution)
        displayed = list(grouped.values())
    for edge in sorted(displayed, key=lambda e: (*e.key, e.metadata.get("change", ""))):
        if _excluded_arrow(view, edge, exclude_arrows, exclude_arrows_to, exclude_arrows_from):
            continue
        # Package/module containment is expressed by the containers themselves.
        if edge.kind == "contains" and view.nodes[edge.source].kind in {"package", "module"}:
            continue
        status = edge.metadata.get("change", "")
        label = ", ".join(sorted(edge.metadata.get("kinds", {edge.kind}))) + (
            f" ({status})" if status and status != "unchanged" else ""
        )
        lines.append(f"{ids[edge.source]} -> {ids[edge.target]}: {json.dumps(label)} {{")
        if status not in COLORS:
            family = arrow_styles.get(subsystem(view.nodes[edge.source].module))
            stroke = family[2] if family else "#64748b"
            lines.append(f'  style.stroke: "{stroke}"')
        if status in COLORS:
            lines.append(
                f'  style.stroke: "{ {"added": "#15803d", "removed": "#b91c1c", "modified": "#a16207"}[status] }"'
            )
        if edge.resolution["status"] in {"unresolved", "ambiguous", "inferred"} or status == "removed":
            lines.append("  style.stroke-dash: 4")
        if edge.kind == "contains":
            lines.append("  style.opacity: 0.35")
        lines.append("}")
    if not view.nodes:
        lines.append('empty: "No architecture nodes in this view"')
    return "\n".join(lines) + "\n"


def render(
    graph,
    output,
    *,
    level="modules",
    external=False,
    layout="auto",
    timeout=120,
    color_arrows=False,
    optimize=True,
    svg_optimization="medium",
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
):
    executable = shutil.which("d2")
    if not executable:
        raise ValueError("D2 is not installed. Install D2 separately, or use --format d2 to emit source.")
    source = d2_source(
        graph,
        level,
        external,
        color_arrows,
        exclude_arrows,
        exclude_arrows_to,
        exclude_arrows_from,
    )
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    view = project(graph, level, external)
    dense = len(view.nodes) > 500 or len(view.edges) > 250
    layouts = (
        (["elk", "dagre"] if level == "full" or dense else ["tala", "elk", "dagre"])
        if layout == "auto"
        else [layout]
    )
    errors = []
    with tempfile.TemporaryDirectory(prefix="archer-d2-") as directory:
        input_path = Path(directory) / "graph.d2"
        input_path.write_text(source)
        rendered = Path(directory) / ("graph" + output.suffix)
        for engine in layouts:
            try:
                result = subprocess.run(
                    [executable, "--layout", engine, str(input_path), str(rendered)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                errors.append(f"{engine}: exceeded {timeout}s")
                continue
            if result.returncode == 0 and rendered.exists():
                report = {"output": str(output), "layout": engine, "fallbacks": errors}
                if output.suffix.lower() == ".svg" and optimize:
                    data, stats = optimize_svg(rendered.read_bytes(), level=svg_optimization)
                    output.write_bytes(data)
                    report["svg_optimization"] = stats
                else:
                    shutil.copyfile(rendered, output)
                return report
            errors.append(f"{engine}: {result.stderr.strip()}")
    raise ValueError("D2 rendering failed: " + "\n".join(errors))
