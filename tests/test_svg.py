import xml.etree.ElementTree as ET
from io import BytesIO

import pytest
import resvg_py
from PIL import Image, ImageChops, ImageStat

from archer.render.svg import optimize_svg, path_boxes, union_rectangles


def fixture_svg(fill="black", extra="", path="M 10 60 L 180 60"):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" data-d2-version="v0.9.0" width="200" height="120" viewBox="0 0 200 120">
    <defs><linearGradient id="gradient"><stop stop-color="#ffddcc"/><stop offset="1" stop-color="#aaccff"/></linearGradient></defs>
    <rect width="200" height="120" fill="url(#gradient)"/>
    <rect x="60" y="15" width="100" height="85" fill="#ccffee" stroke="#dd5555"/>
    <g opacity="0.85"><path d="{path}" fill="none" stroke="#5533cc" style="stroke-width:4;stroke-dasharray:8 3;" mask="url(#labels)"/></g>
    <mask id="labels" maskUnits="userSpaceOnUse" x="0" y="0" width="200" height="120">
    <rect x="0" y="0" width="200" height="120" fill="white"/>
    <rect x="80" y="50" width="30" height="20" fill="{fill}"/>{extra}</mask></svg>'''.encode()


def pixels(svg, width=800):
    return Image.open(BytesIO(resvg_py.svg_to_bytes(svg_string=svg.decode(), width=width))).convert("RGB")


@pytest.mark.parametrize("fill", ["black", "rgba(0,0,0,0.75)", "rgba(0,0,0,0.25)"])
def test_preserves_pixels_over_colored_and_gradient_backgrounds(fill):
    raw = fixture_svg(fill, '<rect x="95" y="45" width="30" height="25" fill="black"/>')
    optimized, stats = optimize_svg(raw)
    assert ImageChops.difference(pixels(raw), pixels(optimized)).getbbox() is None
    assert stats["optimized_paths"] == 1
    assert stats["local_masks"] == (fill != "black")
    assert optimize_svg(optimized)[0] == optimized


def test_curves_and_markers_and_dash_geometry_survive():
    raw = fixture_svg(path="M 10 60 C 30 10 60 100 90 60 S 150 100 180 60")
    raw = raw.replace(
        b"<g opacity=",
        b'<marker id="arrow" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" refX="5" refY="5"><path d="M0 0L10 5L0 10Z" fill="purple"/></marker><g opacity=',
    )
    raw = raw.replace(b'mask="url(#labels)"', b'mask="url(#labels)" marker-end="url(#arrow)"')
    optimized, _ = optimize_svg(raw)
    before, after = ET.fromstring(raw), ET.fromstring(optimized)
    a = next(e for e in before.iter() if e.get("mask"))
    b = next(e for e in after.iter() if e.get("clip-path"))
    assert {k: v for k, v in a.attrib.items() if k != "mask"} == {
        k: v for k, v in b.attrib.items() if k != "clip-path"
    }
    difference = ImageChops.difference(pixels(raw), pixels(optimized))
    assert max(ImageStat.Stat(difference).mean) < 0.01  # subpixel clip antialiasing only


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace(b'fill="black"', b'fill="gray"'),
        lambda s: s.replace(b'fill="black"', b'fill="black" rx="4"'),
        lambda s: s.replace(b"M 10 60 L 180 60", b"M10 60 A40 20 0 0 1 180 60"),
        lambda s: s.replace(b"<g opacity=", b'<g transform="translate(1 1)" opacity='),
        lambda s: s.replace(b'data-d2-version="v0.9.0"', b""),
    ],
)
def test_unknown_features_remain_byte_for_byte_unchanged(change):
    raw = change(fixture_svg())
    optimized, stats = optimize_svg(raw)
    assert optimized == raw
    assert stats["optimized_paths"] == 0


def test_local_masks_cull_unrelated_labels_without_changing_alpha():
    raw = fixture_svg("rgba(0,0,0,0.75)", '<rect x="20" y="100" width="10" height="10" fill="black"/>')
    optimized, stats = optimize_svg(raw)
    assert stats["cutouts_before"] == 2 and stats["cutouts_after"] == 1
    mask = next(e for e in ET.fromstring(optimized).iter() if e.tag.endswith("}mask"))
    assert float(mask.get("height")) < 120
    assert list(mask)[1].get("fill") == "rgba(0,0,0,0.75)"


def test_unused_mask_removed_but_unsupported_references_preserved():
    raw = fixture_svg().replace(b"</svg>", b'<path d="M1 1 Q20 30 40 50" mask="url(#labels)"/></svg>')
    optimized, stats = optimize_svg(raw)
    assert stats["vector_clips"] == 1
    assert any(e.get("id") == "labels" for e in ET.fromstring(optimized).iter())


def test_union_handles_overlaps_without_parity_holes():
    boxes = [(0, 0, 3, 3), (1, 1, 4, 4), (2, 0, 5, 2)]
    union = union_rectangles(boxes)
    for x in (0.5, 1.5, 2.5, 3.5, 4.5):
        for y in (0.5, 1.5, 2.5, 3.5):
            expected = any(a <= x < c and b <= y < d for a, b, c, d in boxes)
            actual = sum(a <= x < c and b <= y < d for a, b, c, d in union)
            assert actual == int(expected)


def test_smooth_curve_reflected_control_is_inside_bounds():
    boxes = path_boxes("M0 0 C1 1 2 100 3 0 S4 0 5 0", 0)
    assert boxes[-1][1] <= -100


def test_css_effects_are_left_unchanged():
    raw = fixture_svg().replace(b"<defs>", b"<style>.connection { filter: blur(3px); }</style><defs>")
    assert optimize_svg(raw)[0] == raw


@pytest.mark.parametrize("fill", ["black", "rgba(0,0,0,0.75)", "rgba(0,0,0,0.25)"])
def test_fast_removes_masks_preserves_fading_and_overlaps(fill):
    raw = fixture_svg(fill, '<rect x="95" y="45" width="30" height="25" fill="rgba(0,0,0,0.5)"/>')
    fast, stats = optimize_svg(raw, "fast")
    assert not list(ET.fromstring(fast).iter("{http://www.w3.org/2000/svg}mask"))
    assert stats["local_masks"] == 0
    assert stats["faded_paths"] > 0
    difference = ImageChops.difference(pixels(raw), pixels(fast))
    assert max(ImageStat.Stat(difference).mean) < 0.1
    assert optimize_svg(fast, "fast")[0] == fast
    medium, _ = optimize_svg(raw)
    upgraded, _ = optimize_svg(medium, "fast")
    assert not list(ET.fromstring(upgraded).iter("{http://www.w3.org/2000/svg}mask"))
    assert ImageChops.difference(pixels(upgraded), pixels(fast)).getbbox() is None


def test_raw_is_byte_exact_and_levels_validated():
    raw = fixture_svg()
    assert optimize_svg(raw, "raw")[0] == raw
    with pytest.raises(ValueError, match="optimization level"):
        optimize_svg(raw, "invalid")


def test_fast_preserves_fonts_text_and_unknown_masks():
    raw = fixture_svg("gray").replace(
        b"<defs>",
        b'<style>@font-face {font-family:embedded;src:url(data:font/woff;base64,AAAA)}</style><text x="5" y="10">Label</text><defs>',
    )
    assert optimize_svg(raw, "fast")[0] == raw
    supported = raw.replace(b'fill="gray"', b'fill="rgba(0,0,0,0.75)"')
    fast, _ = optimize_svg(supported, "fast")
    for name in ("style", "text"):
        a = next(ET.fromstring(supported).iter("{http://www.w3.org/2000/svg}" + name))
        b = next(ET.fromstring(fast).iter("{http://www.w3.org/2000/svg}" + name))
        assert ET.tostring(a) == ET.tostring(b)
