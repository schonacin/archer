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


def kind_style(kind, indent, *, header=False):
    """Keep entity silhouettes stable across projections and color schemes."""
    if header:
        return [f"{indent}shape: text", f"{indent}style.bold: true", f"{indent}style.font-size: 14"]
    radius = 10 if kind == "class" else 20 if kind in {"function", "method"} else 0
    width = 2 if kind == "class" else 1
    return [
        f"{indent}shape: rectangle",
        f"{indent}style.border-radius: {radius}",
        f"{indent}style.stroke-width: {width}",
    ]


def direct_change(node):
    if node is None:
        return None
    status = node.metadata.get("direct_change", node.metadata.get("change"))
    return "modified" if status == "unknown" else status


def change_summary(node):
    if node is None:
        return ""
    parts = []
    for item in node.metadata.get("descendant_changes", []):
        kind = item["kind"]
        count = item["count"]
        plural = "classes" if kind == "class" else kind + "s"
        parts.append(f"{count} {kind if count == 1 else plural} {item['change']}")
    return "\n" + ", ".join(parts) if parts else ""


def nested_nodes(view, level, hidden=()):
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
    # Class ownership comes from declaration edges, not dotted-name guesses.
    classes = {e.source for e in view.edges if e.kind == "contains" and view.nodes[e.source].kind == "class"}
    owners = {e.target: e.source for e in view.edges if e.kind == "contains"}
    class_parents = {}
    for key in view.nodes:
        parent, seen = owners.get(key), {key}
        while parent and parent not in seen:
            if parent in classes:
                class_parents[key] = parent
                break
            seen.add(parent)
            parent = owners.get(parent)
    groups.update(classes)
    parents = {
        name: class_parents.get(name, view.nodes[name].module) if name in classes else name.rpartition(".")[0]
        for name in groups
    }
    group_ids = {}
    for name in sorted(groups, key=lambda name: (name.count("."), name)):
        parent = parents[name]
        local = "g" + hashlib.sha256(name.encode()).hexdigest()[:20]
        group_ids[name] = (group_ids[parent] + "." if parent in group_ids else "") + local
    ids, children, members = {}, {}, {}
    for name in groups:
        parent = parents[name]
        children.setdefault(parent if parent in groups else "", []).append(name)

    def inside(ident, ancestor):
        seen = set()
        while ident and ident not in seen:
            if ident == ancestor:
                return True
            seen.add(ident)
            ident = owners.get(ident)
        return False

    # Dagre cannot connect containers to their descendants. Only those classes
    # need a header endpoint; other relationships attach to the class boundary.
    headers = set()
    for edge in view.edges:
        if edge.kind == "contains":
            continue
        for parent, child in ((edge.source, edge.target), (edge.target, edge.source)):
            if parent in classes and inside(child, parent):
                headers.add(parent)
    for key, node in sorted(view.nodes.items()):
        if key in hidden:
            continue
        if key in classes and key not in headers:
            ids[key] = group_ids[key]
            continue
        parent = node.module if node.module in groups else node.module.rpartition(".")[0]
        parent = key if key in classes else class_parents.get(key, parent)
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
            kind = "class" if name in classes else node.kind if node else "package"
            label += f" [{kind}]" + change_summary(node)
            if node and kind == "class" and node.metadata.get("direct_change") == "unknown":
                label += "\nchange scope unknown"
            status = direct_change(node)
            # A file container summarizes its children; its leaf owns body/imports.
            if kind in {"module", "package"} and status == "modified":
                status = "unchanged"
            color = COLORS.get(status)
            family = styles.get(subsystem(name))
            background = family[0] if family else ("#f8fafc" if depth % 2 == 0 else "#ffffff")
            border = family[2] if family else "#94a3b8"
            if view.metadata.get("diff") and node and node.metadata.get("descendant_changes") and not color:
                border = "#a16207"
            lines.append(f"{indent}{ident}: {json.dumps(label)} {{")
            lines.extend(kind_style(kind, indent + "  "))
            lines.append(f'{indent}  style.fill: "{color or background}"')
            lines.append(f'{indent}  style.stroke: "{border}"')
            if node and node.metadata.get("change") == "removed":
                lines.append(f"{indent}  style.stroke-dash: 4")
            emit(name, depth + 1)
            lines.append(indent + "}")
        for key, local in members.get(parent, []):
            node = view.nodes[key]
            header = key in headers
            if header:
                label = node.qualified_name.rsplit(".", 1)[-1] + " [class]"
            elif key == parent:
                if node.kind in {"module", "package"} and view.metadata.get("diff"):
                    label = (
                        "__init__.py body / imports"
                        if node.kind == "package" and node.file
                        else "module body / imports"
                    )
                    if (
                        node.metadata.get("change") == "modified"
                        and node.metadata.get("direct_change", "unknown") == "unknown"
                    ):
                        label = "file changes (scope unknown)"
                else:
                    label = "__init__.py" if node.kind == "package" and node.file else f"({node.kind})"
            else:
                prefix = parent + "." if parent else ""
                label = node.qualified_name.removeprefix(prefix)
                label += " [" + node.kind + "]"
                label += change_summary(node)
                if node.kind in {"module", "package"} and node.metadata.get("direct_change") == "modified":
                    label += "\nmodule body / imports modified"
                if (
                    node.metadata.get("change") == "modified"
                    and node.metadata.get("direct_change", "unknown") == "unknown"
                ):
                    label += "\nchange scope unknown"
            lines.append(f"{indent}{local}: {json.dumps(label)} {{")
            lines.extend(kind_style(node.kind, indent + "  ", header=header))
            family = styles.get(subsystem(node.module))
            border = family[2] if family else "#64748b"
            # Collapsed scopes retain the signal; body leaves and header anchors
            # represent direct code only, never their owner's descendants.
            if (
                view.metadata.get("diff")
                and key != parent
                and not header
                and node.metadata.get("descendant_changes")
                and direct_change(node) not in COLORS
            ):
                border = "#a16207"
            lines.append(f'{indent}  style.stroke: "{border}"')
            lines.append(f'{indent}  style.font-color: "#0f172a"')
            color = COLORS.get(direct_change(node), family[1] if family else "#f1f5f9")
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


def hidden_initializers(graph, level, initializers):
    if initializers not in {"auto", "all"}:
        raise ValueError(f"Unknown initializer visibility: {initializers}")
    # Audit views preserve every relationship. Older graphs lack classification.
    if (
        initializers == "all"
        or level in {"full", "changes"}
        or graph.metadata.get("diff")
        or graph.metadata.get("focused")
    ):
        return set()
    hidden = {
        key
        for key, node in graph.nodes.items()
        if node.kind == "package"
        and (node.metadata.get("namespace") or node.metadata.get("structural_initializer") is True)
        and not node.metadata.get("parse_error")
    }
    for edge in graph.edges:
        if edge.kind == "contains":
            continue
        hidden.discard(edge.target)
        # Preserve calls, unresolved imports, and dependencies outside the package.
        if (
            edge.kind != "imports"
            or edge.resolution["status"] not in {"exact", "strong"}
            or not graph.nodes[edge.target].module.startswith(edge.source + ".")
        ):
            hidden.discard(edge.source)
    return hidden


def d2_source(
    graph,
    level="modules",
    external=False,
    color_arrows=True,
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
    initializers="auto",
):
    view = project(graph, level, external)
    hidden = hidden_initializers(graph, level, initializers)
    ids, declarations = nested_nodes(view, level, hidden)
    legend = (
        "green=added, red=removed, amber=modified"
        if view.metadata.get("diff")
        else "colors identify subsystems; nesting shows ownership; shapes identify entity kinds"
    )
    lines = ["direction: right", "# Archer IR projection; " + legend]
    if hidden & view.nodes.keys():
        lines.append(
            f"# {len(hidden & view.nodes.keys())} structural package leaves omitted; use --initializers all"
        )
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
        if edge.source not in ids or edge.target not in ids:
            continue
        # Ownership is expressed by containers, including classes.
        if edge.kind == "contains" and view.nodes[edge.source].kind in {"package", "module", "class"}:
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
    else:
        caption = (
            "Square: package / module | Rounded, bold border: class | Rounded: function / method\n"
            "Nesting: ownership | "
            + (
                "Green: added | Red: removed | Amber: direct change (or scope unknown)\nAmber border: changed descendants | Counts summarize descendant changes"
                if view.metadata.get("diff")
                else "Colors: subsystems"
            )
        )
        lines.extend(
            [
                f"archer_legend: {json.dumps(caption)} {{",
                "  shape: text",
                "  near: bottom-center",
                "  style.font-size: 12",
                '  style.font-color: "#475569"',
                "}",
            ]
        )
    return "\n".join(lines) + "\n"


def render(
    graph,
    output,
    *,
    level="modules",
    external=False,
    layout="auto",
    timeout=120,
    color_arrows=True,
    optimize=True,
    svg_optimization="fast",
    exclude_arrows=(),
    exclude_arrows_to=(),
    exclude_arrows_from=(),
    initializers="auto",
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
        initializers,
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
