"""The experimental exporter owns its hierarchy and geometry contract."""

import json
import math
import re
import shutil

import pytest

from archer.cli import default_output, main, parser
from archer.render.experimental import ROOT, _routes, build_map, render_html
from archer.render.html import _render_scene
from archer.render.scenes import Scene, SceneEdge, SceneNode
from archer.scan import scan_sources


def fixture_graph():
    return scan_sources(
        {
            "pkg/__init__.py": "from .service import run\ndef entry(): return run()\n",
            "pkg/service.py": "from .model import Model\ndef run(): return Model().work()\n",
            "pkg/model.py": "class Model:\n def work(self):\n  def inner(): return 1\n  return inner()\n",
        }
    )


def test_ownership_supports_functions_nested_scopes_and_initializer_regions():
    data = build_map(fixture_graph())
    entities = data["entities"]
    assert entities["pkg"]["parent"] == ROOT
    assert entities["pkg.entry"]["parent"] == "@initializer:pkg"
    assert entities["@initializer:pkg"]["children"] == ["pkg.entry"]
    assert entities["pkg.service"]["children"] == ["pkg.service.run"]
    assert entities["pkg.model.Model.work.inner"]["parent"] == "pkg.model.Model.work"
    assert any(
        e["source"] == "pkg.service.run" and e["target"] == "pkg.model.Model.work"
        for e in data["relationships"]
    )
    assert any(
        e["source"] == "@initializer:pkg" and e["canonicalSource"] == "pkg" for e in data["relationships"]
    )


def test_exclusions_keep_entities_and_do_not_mutate_scan_diagnostics():
    graph = fixture_graph()
    graph.metadata["diagnostics"] = []
    data = build_map(graph, exclude_arrows=("pkg.service",))
    assert "pkg.service.run" in data["entities"]
    assert not any(
        e["source"].startswith("pkg.service") or e["target"].startswith("pkg.service")
        for e in data["relationships"]
    )
    data["diagnostics"].append("layout note")
    assert graph.metadata["diagnostics"] == []


def test_cli_setting_is_opt_in_and_has_a_distinct_default_filename(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = parser().parse_args(["render", "--format", "html", "--html-viewer", "experimental"])
    assert default_output(args, fixture_graph()).name == "architecture-explorer-experimental.html"
    assert parser().parse_args(["render", "--format", "html"]).html_viewer == "classic"
    assert main(["render", "--html-viewer", "experimental"]) == 2
    assert "only be used with --format html" in capsys.readouterr().err
    assert (
        main(["render", "--format", "html", "--html-viewer", "experimental", "--html-view-mode", "isolated"])
        == 2
    )
    assert "classic HTML viewer" in capsys.readouterr().err
    reference = parser().parse_args(
        ["render", "--format", "html", "--html-viewer", "experimental", "--html-map-layout", "reference"]
    )
    assert default_output(reference, fixture_graph()).name.endswith("experimental-reference.html")
    assert main(["render", "--format", "html", "--html-map-layout", "reference"]) == 2
    assert "experimental HTML viewer" in capsys.readouterr().err


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 is required for geometry export")
def test_actual_geometry_portable_export_and_empty_graph(tmp_path):
    graph = fixture_graph()
    graph.nodes["pkg.service.run"].qualified_name = '</script><script>alert("x")</script>{{SCRIPT}}'
    output = tmp_path / "map.html"
    report = render_html(graph, output, layout="tala")
    page = output.read_text()
    assert report["viewer"] == "experimental"
    assert "archer-scenes" not in page and "__ARCHER_METRICS__" not in page
    assert '</script><script>alert("x")</script>{{SCRIPT}}' not in page
    data = json.loads(
        re.search(r'<script id="map-data" type="application/json">(.*?)</script>', page, re.DOTALL).group(1)
    )
    assert data["entities"]["pkg.service.run"]["name"].endswith("{{SCRIPT}}")
    for owner, layout in data["layouts"].items():
        assert set(layout["nodes"]) == set(data["entities"][owner]["children"])
        assert all(math.isfinite(n) for n in layout["bounds"])
        assert min(layout["bounds"][2:]) > 0
        for edge in layout["edges"]:
            assert edge["source"] in layout["nodes"] and edge["target"] in layout["nodes"]
            assert edge["paths"] and all(p["d"].startswith("M") for p in edge["paths"])
    empty = tmp_path / "empty.html"
    assert render_html(scan_sources({}), empty)["entities"] == 0
    assert '"bounds":[0,0,640,400]' in empty.read_text()


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 is required for geometry export")
def test_reference_placement_retains_tala_at_every_hierarchy_level(tmp_path):
    graph = fixture_graph()
    output = tmp_path / "reference.html"
    report = render_html(graph, output, layout="tala", map_layout="reference")
    assert report["mapLayout"] == "reference"
    data = json.loads(
        re.search(
            r'<script id="map-data" type="application/json">(.*?)</script>', output.read_text(), re.DOTALL
        ).group(1)
    )
    assert data["build"]["mapLayout"] == "reference"
    assert all(
        scene["spacing"] == "reference" and scene["engine"] == "tala" for scene in data["layouts"].values()
    )
    assert len(data["layouts"]["pkg"]["nodes"]) == 3
    assert data["layouts"]["pkg"]["edges"]
    assert all(
        path["transform"][0] > 0
        for scene in data["layouts"].values()
        for edge in scene["edges"]
        for path in edge["paths"]
    )
    with pytest.raises(ValueError, match="compact or reference"):
        render_html(graph, output, map_layout="unknown")


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 is required for geometry export")
def test_tala_connector_geometry_uses_same_coordinate_space_as_nodes(tmp_path):
    scene = Scene(
        "root",
        "modules",
        None,
        None,
        None,
        "Root",
        nodes=[
            SceneNode("a", "a", "A", "module", width=280, height=100),
            SceneNode("b", "b", "B", "module", width=280, height=100),
        ],
        edges=[SceneEdge("edge", "a", "b", {"calls"}, color="#64748b")],
    )
    svg, _, nodes, _, _, _ = _render_scene(shutil.which("d2"), scene, tmp_path, "tala", 120, "raw", True)
    route = _routes(svg)["edge"]
    assert len(route) == 1
    from archer.render.svg import path_boxes

    s, tx, _ty = route[0]["transform"]
    boxes = path_boxes(route[0]["d"], 0)
    left, right = min(b[0] for b in boxes) * s + tx, max(b[2] for b in boxes) * s + tx
    a, b = (next(n["bounds"] for n in nodes if n["entityId"] == key) for key in ("a", "b"))
    assert left >= min(a[0], b[0]) and right <= max(a[0] + a[2], b[0] + b[2])
