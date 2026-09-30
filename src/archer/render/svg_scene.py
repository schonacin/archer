"""D2 0.9 SVG adapter for semantic scenes."""

from __future__ import annotations

import base64
import math
import re
import xml.etree.ElementTree as ET

from archer.render.scenes import Scene

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)


def _tag(name):
    return f"{{{SVG}}}{name}"


def _d2_keys(scene: Scene):
    by_visual = {node.visual_id: node for node in scene.nodes}
    keys = {}

    def resolve(visual_id, stack=()):
        if visual_id in keys:
            return keys[visual_id]
        if visual_id in stack:
            raise ValueError(f"Scene {scene.scene_id}: visual containment cycle involving {visual_id}")
        node = by_visual[visual_id]
        parent = node.metadata.get("parentVisualId")
        if parent is not None and parent not in by_visual:
            raise ValueError(f"Scene {scene.scene_id}: missing visual parent {parent}")
        keys[visual_id] = resolve(parent, (*stack, visual_id)) + "." + visual_id if parent else visual_id
        return keys[visual_id]

    for visual_id in by_visual:
        resolve(visual_id)
    return keys


def _edge_identity(source, target):
    a, b = source.split("."), target.split(".")
    common = 0
    while common < min(len(a), len(b)) and a[common] == b[common]:
        common += 1
    if common in {len(a), len(b)}:
        return f"({source} -> {target})["
    prefix = ".".join(a[:common])
    relative_source = ".".join(a[common:])
    relative_target = ".".join(b[common:])
    body = f"({relative_source} -> {relative_target})["
    return prefix + "." + body if prefix else body


def scene_source(scene: Scene, *, color_arrows=True):
    lines = ["direction: right", "# Archer semantic scene"]
    if not scene.nodes:
        lines += ['empty: "Nothing in this view" {', "  shape: rectangle", "}"]
    children = {}
    for node in scene.nodes:
        children.setdefault(node.metadata.get("parentVisualId"), []).append(node)

    def emit_node(node, depth):
        indent = "  " * depth
        display = node.metadata.get("displayLabel", node.label)
        if node.child_scene_id:
            display = display[:47] + "…" if len(display) > 48 else display
            display = "\n".join(display[index : index + 24] for index in range(0, len(display), 24))
            display += f" [{node.kind}]"
        label = display.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        lines.append(f'{indent}{node.visual_id}: "{label}" {{')
        if node.role == "frame":
            styles = [
                "shape: rectangle",
                f'style.fill: "{node.metadata.get("fill", "#ffffff")}"',
                f'style.stroke: "{node.metadata.get("stroke", "#64748b")}"',
                "style.stroke-width: 2",
                f"style.border-radius: {node.metadata.get('borderRadius', 0)}",
            ]
            if node.width and node.height:
                styles += [f"width: {node.width}", f"height: {node.height}"]
        elif node.role == "owner":
            styles = [
                "shape: rectangle",
                "width: 180",
                "height: 44",
                "style.border-radius: 10",
                "style.stroke-width: 2",
            ]
        elif node.role == "context":
            styles = [
                "shape: rectangle",
                "style.stroke-dash: 4",
                'style.fill: "#f8fafc"',
                'style.stroke: "#94a3b8"',
            ]
        elif node.role == "group":
            styles = [
                "shape: rectangle",
                f'style.fill: "{node.metadata.get("fill", "#ffffff")}"',
                f'style.stroke: "{node.metadata.get("stroke", "#94a3b8")}"',
            ]
        elif node.role == "empty":
            styles = ["shape: rectangle", 'style.fill: "#f8fafc"', 'style.stroke: "#cbd5e1"']
        else:
            styles = ["shape: rectangle"]
            if node.kind == "class":
                styles += ["style.border-radius: 10", "style.stroke-width: 2"]
            elif node.kind in {"method", "function"}:
                styles.append("style.border-radius: 20")
            if node.width and node.height:
                styles += [f"width: {node.width}", f"height: {node.height}"]
            if node.metadata.get("fill"):
                styles.append(f'style.fill: "{node.metadata["fill"]}"')
            if node.metadata.get("stroke"):
                styles.append(f'style.stroke: "{node.metadata["stroke"]}"')
        lines.extend(f"{indent}  {style}" for style in styles)
        for child in sorted(children.get(node.visual_id, []), key=lambda item: item.visual_id):
            emit_node(child, depth + 1)
        lines.append(indent + "}")

    for node in sorted(children.get(None, []), key=lambda item: item.visual_id):
        emit_node(node, 0)
    d2_keys = _d2_keys(scene)
    for edge in scene.edges:
        label = ", ".join(sorted(edge.kinds))
        if edge.relationship_count > 1:
            label += f" ×{edge.relationship_count}"
        lines.append(f'{d2_keys[edge.source_visual_id]} -> {d2_keys[edge.target_visual_id]}: "{label}" {{')
        if edge.dashed:
            lines.append("  style.stroke-dash: 4")
        lines.append(f'  style.stroke: "{edge.color if color_arrows else "#64748b"}"')
        lines.append("}")
    return "\n".join(lines) + "\n"


def _decoded_classes(element):
    for value in element.get("class", "").split():
        try:
            yield base64.b64decode(value, validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            pass


def _numbers(value):
    return [float(x) for x in re.split(r"[ ,]+", value.strip())]


def _compose(outer, inner):
    """Compose supported uniform affine transforms: outer(inner(point))."""
    oscale, ox, oy = outer
    iscale, ix, iy = inner
    return oscale * iscale, oscale * ix + ox, oscale * iy + oy


def _transform(value):
    result = (1.0, 0.0, 0.0)
    if not value:
        return result
    position = 0
    for match in re.finditer(r"([A-Za-z]+)\s*\(([^)]*)\)", value):
        if value[position : match.start()].strip(" ,\t\r\n"):
            raise ValueError("Unsupported SVG transform syntax")
        name, arguments = match.group(1), _numbers(match.group(2))
        if name == "translate" and len(arguments) in {1, 2}:
            operation = (1.0, arguments[0], arguments[1] if len(arguments) == 2 else 0.0)
        elif (
            name == "scale"
            and len(arguments) in {1, 2}
            and (len(arguments) == 1 or math.isclose(arguments[0], arguments[1]))
        ):
            operation = (arguments[0], 0.0, 0.0)
        elif name == "matrix" and len(arguments) == 6:
            a, b, c, d, e, f = arguments
            if not math.isclose(b, 0) or not math.isclose(c, 0) or not math.isclose(a, d):
                raise ValueError("Unsupported rotated, skewed, or nonuniform SVG matrix")
            operation = (a, e, f)
        else:
            raise ValueError(f"Unsupported SVG transform: {name}")
        if operation[0] <= 0 or not all(math.isfinite(number) for number in operation):
            raise ValueError("SVG transforms require a positive finite uniform scale")
        result = _compose(result, operation)
        position = match.end()
    if value[position:].strip(" ,\t\r\n"):
        raise ValueError("Unsupported SVG transform syntax")
    return result


def adapt_svg(data: bytes, scene: Scene):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"Scene {scene.scene_id}: invalid SVG: {exc}") from exc
    if root.get("data-d2-version") != "v0.9.0":
        raise ValueError(
            f"Scene {scene.scene_id}: unsupported D2 SVG adapter version {root.get('data-d2-version')!r}; expected v0.9.0"
        )
    forbidden = {
        _tag("script"),
        _tag("foreignObject"),
        _tag("iframe"),
        _tag("object"),
        _tag("animate"),
        _tag("animateMotion"),
        _tag("animateTransform"),
        _tag("set"),
    }
    for element in root.iter():
        if element.tag in forbidden or any(key.lower().startswith("on") for key in element.attrib):
            raise ValueError(f"Scene {scene.scene_id}: unsupported active SVG content")
        for key in ("href", f"{{{XLINK}}}href"):
            value = element.get(key)
            if value and not value.startswith(("#", "data:")):
                raise ValueError(f"Scene {scene.scene_id}: external SVG resource is not portable")
        for value in element.attrib.values():
            for match in re.finditer(r"url\(([^)]+)\)", value, re.IGNORECASE):
                target = match.group(1).strip(" \t\r\n'\"")
                if not target.startswith(("#", "data:")):
                    raise ValueError(f"Scene {scene.scene_id}: external SVG resource is not portable")

    by_visual = {node.visual_id: node for node in scene.nodes}
    d2_keys = _d2_keys(scene)
    visual_by_d2 = {value: key for key, value in d2_keys.items()}
    node_elements = {}
    edge_elements = {}
    for element in root.iter(_tag("g")):
        decoded = list(_decoded_classes(element))
        for value in decoded:
            if value in visual_by_d2:
                node_elements[visual_by_d2[value]] = element
            for edge in scene.edges:
                identity = _edge_identity(d2_keys[edge.source_visual_id], d2_keys[edge.target_visual_id])
                prefixes = (
                    identity,
                    identity.replace(" -> ", " -&gt; "),
                )
                if value.startswith(prefixes):
                    edge_elements[edge.visual_id] = element
    missing = sorted(set(by_visual) - set(node_elements))
    if missing:
        raise ValueError(
            f"Scene {scene.scene_id}: D2 identity adapter did not find node(s): {', '.join(missing)}"
        )
    missing_edges = sorted({edge.visual_id for edge in scene.edges} - set(edge_elements))
    if missing_edges:
        raise ValueError(
            f"Scene {scene.scene_id}: D2 identity adapter did not find connector(s): {', '.join(missing_edges)}"
        )

    outer_vb = _numbers(root.get("viewBox", ""))
    if len(outer_vb) != 4 or not all(math.isfinite(x) for x in outer_vb) or min(outer_vb[2:]) <= 0:
        raise ValueError(f"Scene {scene.scene_id}: invalid SVG viewBox")
    nested = next((x for x in root if x.tag == _tag("svg")), None)
    nested_vb = _numbers(nested.get("viewBox", "")) if nested is not None else outer_vb
    if len(nested_vb) != 4 or min(nested_vb[2:]) <= 0:
        raise ValueError(f"Scene {scene.scene_id}: invalid nested SVG viewBox")
    if nested is not None and scene.metadata.get("frameVisualId"):
        canvas = next(
            (
                element
                for element in nested
                if element.tag == _tag("rect")
                and all(
                    math.isclose(actual, expected)
                    for actual, expected in zip(
                        [
                            float(element.get("x", 0)),
                            float(element.get("y", 0)),
                            float(element.get("width", 0)),
                            float(element.get("height", 0)),
                        ],
                        nested_vb,
                        strict=True,
                    )
                )
                and float(element.get("stroke-width", 0)) == 0
            ),
            None,
        )
        if canvas is None:
            raise ValueError(f"Scene {scene.scene_id}: isolated view has no identifiable D2 canvas")
        style = canvas.get("style", "").rstrip(";")
        canvas.set("style", (style + ";" if style else "") + "fill:transparent")
        canvas.set("fill", "none")
        canvas.set("data-archer-canvas", "true")
    parent_map = {child: parent for parent in root.iter() for child in parent}
    if nested is not None:
        width = float(nested.get("width", outer_vb[2]))
        height = float(nested.get("height", outer_vb[3]))
        scale = min(width / nested_vb[2], height / nested_vb[3])
        nested_map = (
            scale,
            float(nested.get("x", 0)) - scale * nested_vb[0],
            float(nested.get("y", 0)) - scale * nested_vb[1],
        )
    else:
        nested_map = (1.0, 0.0, 0.0)

    manifest_nodes = []
    for visual, element in node_elements.items():
        node = by_visual[visual]
        element.set("data-visual-id", visual)
        element.set("data-role", node.role)
        element.set("role", "group")
        element.set("aria-label", node.label)
        if node.entity_id is not None:
            element.set("data-archer-id", node.entity_id)
        boundary = next((x for x in element.iter() if x.tag == _tag("rect") and x.get("width")), None)
        if boundary is None:
            raise ValueError(f"Scene {scene.scene_id}: unsupported boundary geometry for {visual}")
        boundary.set("data-archer-boundary", "true")
        try:
            geometry_map = (1.0, 0.0, 0.0)
            current = boundary
            while current is not None and current is not nested:
                geometry_map = _compose(_transform(current.get("transform")), geometry_map)
                current = parent_map.get(current)
            if nested is not None:
                geometry_map = _compose(_transform(nested.get("transform")), geometry_map)
            geometry_map = _compose(nested_map, geometry_map)
            scale, translate_x, translate_y = geometry_map
            bounds = [
                scale * float(boundary.get("x", 0)) + translate_x,
                scale * float(boundary.get("y", 0)) + translate_y,
                scale * float(boundary.get("width")),
                scale * float(boundary.get("height")),
            ]
        except ValueError as exc:
            raise ValueError(
                f"Scene {scene.scene_id}: invalid boundary geometry for {visual}: {exc}"
            ) from exc
        if not all(math.isfinite(x) for x in bounds) or min(bounds[2:]) <= 0:
            raise ValueError(f"Scene {scene.scene_id}: non-positive boundary geometry for {visual}")
        manifest_nodes.append(
            {
                "visualId": visual,
                "entityId": node.entity_id,
                "kind": node.kind,
                "role": node.role,
                "bounds": bounds,
                "childSceneId": node.child_scene_id,
                "label": node.label,
            }
        )
    for visual, element in edge_elements.items():
        element.set("data-visual-id", visual)
        element.set("data-role", "edge")
        edge = next(item for item in scene.edges if item.visual_id == visual)
        source = by_visual[edge.source_visual_id].label
        target = by_visual[edge.target_visual_id].label
        element.set("role", "group")
        element.set("aria-label", f"{source} to {target}: {', '.join(sorted(edge.kinds))}")

    # Namespace every resource identifier and fragment URL.  Attribute-level
    # rewriting covers D2 0.9's marker/mask/clip output; unsupported CSS url()
    # forms are rejected instead of guessed at.
    prefix = scene.scene_id + "-"
    ids = {element.get("id") for element in root.iter() if element.get("id")}
    for style in root.iter(_tag("style")):
        text = style.text or ""
        if "url(#" in text:
            raise ValueError(f"Scene {scene.scene_id}: unsupported CSS resource reference")
        if re.search(r"@import\b", text, re.IGNORECASE):
            raise ValueError(f"Scene {scene.scene_id}: external CSS import is not portable")
        for match in re.finditer(r"url\(([^)]+)\)", text, re.IGNORECASE):
            target = match.group(1).strip(" \t\r\n'\"")
            if not target.startswith("data:"):
                raise ValueError(f"Scene {scene.scene_id}: external CSS resource is not portable")
    for element in root.iter():
        if element.get("id"):
            element.set("id", prefix + element.get("id"))
        for key, value in list(element.attrib.items()):
            for old in ids:
                value = value.replace(f"url(#{old})", f"url(#{prefix}{old})")
                if value == f"#{old}":
                    value = f"#{prefix}{old}"
            if key in {"aria-labelledby", "aria-describedby"}:
                value = " ".join(prefix + item if item in ids else item for item in value.split())
            element.set(key, value)
    root.set("data-scene-id", scene.scene_id)
    root.set("class", (root.get("class", "") + " archer-scene-svg").strip())
    return (
        ET.tostring(root, encoding="unicode"),
        outer_vb,
        sorted(manifest_nodes, key=lambda n: n["visualId"]),
    )
