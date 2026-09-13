"""Selectable post-processing for D2's diagram-wide label masks.

Medium replaces opaque cutouts with clips and bounds translucent masks locally.
Fast uses clipped copies of connector geometry with equivalent opacity, trading
small clip-boundary compositing differences for maskless rendering. Raw is exact.
Fonts, geometry, colors and draw order survive; unknown constructs stay untouched.
"""

import math
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from itertools import pairwise

SVG = "http://www.w3.org/2000/svg"
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
TOKEN = re.compile(r"[MLCS]|" + NUMBER)
ET.register_namespace("", SVG)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def tag(name):
    return f"{{{SVG}}}{name}"


def intersect(a, b):
    x, y, right, bottom = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x, y, right, bottom) if x < right and y < bottom else None


def rect(element):
    x, y = float(element.get("x", 0)), float(element.get("y", 0))
    w, h = float(element.attrib["width"]), float(element.attrib["height"])
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise ValueError("Invalid rectangle")
    return x, y, x + w, y + h


def rect_attrs(bounds):
    x, y, right, bottom = bounds
    return {
        k: format(v, ".15g") for k, v in zip(("x", "y", "width", "height"), (x, y, right - x, bottom - y))
    }


def path_boxes(data, padding):
    """Conservative control-hull bounds for D2's absolute M/L/C/S commands."""
    if TOKEN.sub("", data).strip(" \t\r\n,"):
        raise ValueError("Unsupported path command")
    tokens = TOKEN.findall(data)
    boxes, current, control, command = [], None, None, None
    i = 0
    while i < len(tokens):
        if tokens[i] in {"M", "L", "C", "S"}:
            command = tokens[i]
            i += 1
        count = {"M": 2, "L": 2, "C": 6, "S": 4}.get(command)
        if count is None or i + count > len(tokens):
            raise ValueError("Invalid path")
        values = list(map(float, tokens[i : i + count]))
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Nonfinite path")
        points = list(zip(values[::2], values[1::2]))
        i += count
        if command != "M":
            if current is None:
                raise ValueError("Missing move")
            if command == "S":
                points.insert(
                    0, (2 * current[0] - control[0], 2 * current[1] - control[1]) if control else current
                )
            points.insert(0, current)
        current = points[-1]
        control = points[-2] if command in {"C", "S"} else None
        if command == "M":
            command = "L"
        boxes.append(
            (
                min(p[0] for p in points) - padding,
                min(p[1] for p in points) - padding,
                max(p[0] for p in points) + padding,
                max(p[1] for p in points) + padding,
            )
        )
    if not boxes:
        raise ValueError("Empty path")
    return boxes


def union_rectangles(rectangles):
    """Disjoint rectangles for a union, including overlapping label cutouts."""
    xs = sorted({x for r in rectangles for x in (r[0], r[2])})
    result, previous = [], {}
    for left, right in pairwise(xs):
        intervals = sorted((r[1], r[3]) for r in rectangles if r[0] < right and r[2] > left)
        merged = []
        for low, high in intervals:
            if merged and low <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
            else:
                merged.append((low, high))
        active = {}
        for low, high in merged:
            key = low, high
            if key in previous:
                index = previous[key]
                result[index] = (result[index][0], low, right, high)
            else:
                index = len(result)
                result.append((left, low, right, high))
            active[key] = index
        previous = active
    return result


def contour(bounds, reverse=False):
    x, y, right, bottom = (format(v, ".15g") for v in bounds)
    return f"M{x} {y}V{bottom}H{right}V{y}Z" if reverse else f"M{x} {y}H{right}V{bottom}H{x}Z"


def label_mask(mask):
    allowed = {"id", "maskUnits", "maskContentUnits", "x", "y", "width", "height"}
    if (
        set(mask.attrib) - allowed
        or mask.get("maskUnits") != "userSpaceOnUse"
        or mask.get("maskContentUnits", "userSpaceOnUse") != "userSpaceOnUse"
    ):
        raise ValueError("Unsupported mask")
    elements = list(mask)
    if not elements or elements[0].get("fill") != "white":
        raise ValueError("Not a label mask")
    canvas = rect(mask)
    holes = []
    for i, element in enumerate(elements):
        if element.tag != tag("rect") or set(element.attrib) - {"x", "y", "width", "height", "fill"}:
            raise ValueError("Nonrectangular mask")
        bounds = rect(element)
        if i == 0:
            if bounds != canvas:
                raise ValueError("Incomplete white backdrop")
            continue
        fill = element.get("fill", "")
        partial = re.fullmatch(r"rgba\(0,\s*0,\s*0,\s*(" + NUMBER + r")\)", fill)
        alpha = 1.0 if fill in {"black", "#000", "#000000"} else float(partial[1]) if partial else -1
        if not 0 <= alpha <= 1:
            raise ValueError("Unsupported mask fill")
        clipped = intersect(canvas, bounds)
        if clipped and alpha:
            holes.append((clipped, alpha, element))
    return canvas, holes


def transmission_regions(holes):
    """Disjoint rectangles grouped by transmission, including overlapping cutouts."""
    xs = sorted({x for bounds, alpha in holes for x in (bounds[0], bounds[2])})
    groups = {}
    for left, right in pairwise(xs):
        active = [(b, a) for b, a in holes if b[0] < right and b[2] > left]
        ys = sorted({y for b, a in active for y in (b[1], b[3])})
        for top, bottom in pairwise(ys):
            alphas = [a for b, a in active if b[1] < bottom and b[3] > top]
            if alphas:
                transmission = math.prod(1 - a for a in alphas)
                if transmission:
                    groups.setdefault(transmission, []).append((left, top, right, bottom))
    return {alpha: union_rectangles(boxes) for alpha, boxes in groups.items()}


def optimize_svg(data, level="medium"):
    """Return SVG bytes and measurable optimization counts; safe to run twice."""
    if level not in {"raw", "medium", "fast"}:
        raise ValueError(f"Unknown SVG optimization level: {level}")
    original = data.encode() if isinstance(data, str) else data
    stats = {
        "level": level,
        "faded_paths": 0,
        "optimized_paths": 0,
        "vector_clips": 0,
        "local_masks": 0,
        "unmasked_paths": 0,
        "cutouts_before": 0,
        "cutouts_after": 0,
    }
    if level == "raw":
        return original, stats
    try:
        root = ET.fromstring(original)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid rendered SVG: {exc}") from exc
    if not root.get("data-d2-version"):
        return original, stats
    # CSS effects can extend geometry beyond the conservative stroke bounds.
    if any(re.search(r"(?:transform|filter|animation)\s*:", el.text or "") for el in root.iter(tag("style"))):
        return original, stats
    parents = {child: parent for parent in root.iter() for child in parent}
    ids = {e.get("id"): e for e in root.iter() if e.get("id")}
    masks = {}
    for ident, element in ids.items():
        if element.tag == tag("mask") and (level == "fast" or not ident.startswith("archer-svg-")):
            try:
                masks[ident] = label_mask(element)
            except (ValueError, KeyError):
                pass
    generated = ET.Element(tag("defs"))
    for path in list(root.iter(tag("path"))):
        match = re.fullmatch(r"url\(#([^)]*)\)", path.get("mask", ""))
        if not match or match[1] not in masks or path.get("clip-path"):
            continue
        current = path
        unsafe = False
        while current is not None:
            if any(k in current.attrib for k in ("transform", "filter")) or any(
                k in current.get("style", "") for k in ("transform", "filter", "animation")
            ):
                unsafe = True
                break
            current = parents.get(current)
        if unsafe or (level == "fast" and path.get("id")):
            continue
        try:
            width = re.search(r"stroke-width:\s*(" + NUMBER + ")", path.get("style", ""))
            padding = max(16, float(width[1] if width else path.get("stroke-width", 2)) * 8)
            for key in ("marker-start", "marker-mid", "marker-end"):
                if path.get(key):
                    ref = re.fullmatch(r"url\(#([^)]*)\)", path.get(key))
                    marker = ids.get(ref[1]) if ref else None
                    if marker is None or marker.get("markerUnits") != "userSpaceOnUse":
                        raise ValueError("Unsupported marker units")
                    padding = max(
                        padding,
                        math.hypot(float(marker.get("markerWidth", 3)), float(marker.get("markerHeight", 3)))
                        + abs(float(marker.get("refX", 0)))
                        + abs(float(marker.get("refY", 0))),
                    )
            boxes = path_boxes(path.attrib["d"], padding)
            canvas, holes = masks[match[1]]
            bounds = intersect(
                canvas,
                (
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                ),
            )
            if not bounds:
                continue
            relevant = [
                (b, alpha, element) for b, alpha, element in holes if any(intersect(b, box) for box in boxes)
            ]
        except (ValueError, KeyError):
            continue
        ident = f"archer-svg-{stats['optimized_paths']}"
        while ident in ids:
            ident += "x"
        ids[ident] = path
        path.attrib.pop("mask")
        if not relevant and all(intersect(canvas, box) == box for box in boxes):
            stats["unmasked_paths"] += 1
        elif level == "fast" or all(alpha == 1 for _, alpha, _ in relevant):
            clip = ET.SubElement(generated, tag("clipPath"), {"id": ident, "clipPathUnits": "userSpaceOnUse"})
            holes_union = union_rectangles([intersect(b, bounds) for b, _, _ in relevant])
            ET.SubElement(
                clip,
                tag("path"),
                {
                    "d": contour(bounds) + "".join(contour(b, True) for b in holes_union),
                    "clip-rule": "nonzero",
                },
            )
            path.set("clip-path", f"url(#{ident})")
            stats["vector_clips"] += 1
            if level == "fast":
                regions = transmission_regions([(intersect(b, bounds), a) for b, a, _ in relevant])
                parent = parents[path]
                index = list(parent).index(path) + 1
                for number, (transmission, rectangles) in enumerate(sorted(regions.items())):
                    region_id = f"{ident}-fade-{number}"
                    while region_id in ids:
                        region_id += "x"
                    ids[region_id] = path
                    region_clip = ET.SubElement(
                        generated,
                        tag("clipPath"),
                        {
                            "id": region_id,
                            "clipPathUnits": "userSpaceOnUse",
                        },
                    )
                    ET.SubElement(
                        region_clip,
                        tag("path"),
                        {
                            "d": "".join(contour(b) for b in rectangles),
                        },
                    )
                    group = ET.Element(tag("g"), {"opacity": format(transmission, ".15g")})
                    clone = deepcopy(path)
                    clone.set("clip-path", f"url(#{region_id})")
                    group.append(clone)
                    parent.insert(index, group)
                    index += 1
                    stats["faded_paths"] += 1
                    stats["vector_clips"] += 1
        else:
            mask = ET.SubElement(
                generated, tag("mask"), {"id": ident, "maskUnits": "userSpaceOnUse", **rect_attrs(bounds)}
            )
            ET.SubElement(mask, tag("rect"), {**rect_attrs(bounds), "fill": "white"})
            for _, _, element in relevant:
                mask.append(deepcopy(element))
            path.set("mask", f"url(#{ident})")
            stats["local_masks"] += 1
        stats["optimized_paths"] += 1
        stats["cutouts_before"] += len(holes)
        stats["cutouts_after"] += len(relevant)
    if not stats["optimized_paths"]:
        return original, stats
    # Keep original masks if an unsupported path still references them.
    remaining = {e.get("mask") for e in root.iter()}
    for ident in masks:
        element = ids[ident]
        if f"url(#{ident})" not in remaining:
            parents[element].remove(element)
    root.append(generated)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), stats
