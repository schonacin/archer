import base64
import json
import re
import shutil
import xml.etree.ElementTree as ET
from importlib.resources import files

import pytest

from archer.cli import main
from archer.graph.diff import diff
from archer.render.camera import compose, inverse_rebase, portal_embedding
from archer.render.html import validate_manifest
from archer.render.scenes import Scene, SceneNode, _dimensions, build_scenes, scene_id
from archer.render.svg_scene import adapt_svg
from archer.scan import scan_sources


def fixture_graph():
    return scan_sources(
        {
            "p/a.py": (
                "class A:\n"
                " class Inner: pass\n"
                " def run(self):\n"
                "  def nested(): return 1\n"
                "  return nested()\n"
                "def free():\n"
                " def nested_free(): return 1\n"
                " return nested_free()\n"
            ),
            "p/b.py": "from .a import A\nclass B(A): pass\nA().run()\n",
            "q/c.py": (
                "class Empty: pass\n"
                "class TrèsLongUnicodeArchitectureComponentNameForEscaping:\n"
                " def execute(self): return unresolved_target()\n"
            ),
        }
    )


def test_viewer_keyboard_navigation_controls_are_packaged():
    js = files("archer.render.assets").joinpath("viewer.js").read_text(encoding="utf-8")
    assert 'document.addEventListener("keydown"' in js
    assert 'event.target.closest("input, select, textarea")' in js
    assert "function effectivePanStep()" in js
    assert 'localStorage.getItem("archer:navigation:v1")' in js
    assert "function applyTransitionState(state, progress)" in js
    assert "setFillOpacity(state.childRoles.frameBoundaries" in js
    assert 'applyTransitionState(state, 0);\n      portal.style.display = "block"' in js
    assert "cancelAnimationFrame(paintFrame)" in js
    assert "const [vx, vy, vw, vh] = scene.frameBounds || scene.viewBox" in js
    assert "if (state.overlapFrames) setElementsOpacity(state.parentRoles.nodes, 1)" in js
    assert 'localStorage.getItem("archer:semantic-zoom:v1")' in js
    assert "const childFit = overlaps ? compose(camera, embedding)" in js
    assert "parentStart, parentEnd: mappedParent" in js
    assert "function syncRetainedLayers(baseCamera = camera, foregroundScene = activeScene())" in js
    assert "retainedLayer.style.display = !occluded" in js
    assert "const retainedLayer = state.overlapFrames ? retainScene(state.parentSvg) : null" in js
    assert "if (item.retainedSvg)" in js
    assert "function evaluateSemanticCamera()" in js
    assert "function parentPanCandidate()" in js
    assert "const parentCamera = inverseRebase(camera, item.embedding)" in js
    assert "evaluateSemanticCamera();" in js
    assert "queuedSemanticEntry = candidate" in js

    html = files("archer.render").joinpath("html.py").read_text(encoding="utf-8")
    assert 'id="pan-speed"' in html
    assert 'id="pan-zoom-scale"' in html
    assert 'id="entry-threshold"' in html
    assert 'id="exit-threshold"' in html
    assert 'id="underlays"' in html


def test_scene_inventory_and_nested_ownership():
    graph = fixture_graph()
    scenes = build_scenes(graph)
    modules = [n for n in graph.nodes.values() if n.kind in {"module", "package"} and n.file]
    classes = [n for n in graph.nodes.values() if n.kind == "class"]
    assert len(scenes) == 1 + len(modules) + len(classes)
    types = scenes[scene_id("types", "p.a")]
    assert {n.entity_id for n in types.nodes if n.role == "node"} == {"p.a.A", "p.a.A.Inner"}
    assert types.metadata["freeFunctionCount"] == 1
    assert scenes[scene_id("symbols", "p.a.A")].metadata["nestedClassCount"] == 1
    # Unrelated relationships must not manufacture boundary context in p.a.
    assert all("p.b.B" not in n.label for n in scenes[scene_id("symbols", "p.a.A.Inner")].nodes)


def test_isolated_views_skip_empty_type_level_and_remove_boundary_context():
    graph = scan_sources(
        {
            "functions.py": "def first(): return second()\ndef second(): return 1\n",
            "model.py": "class Model:\n def run(self): return helper()\ndef helper(): return 1\n",
            "outside.py": "from model import Model\nModel().run()\n",
        }
    )
    scenes = build_scenes(graph, external=True, view_mode="isolated")

    function_module = next(node for node in scenes["root"].nodes if node.entity_id == "functions")
    function_scene = scenes[function_module.child_scene_id]
    assert function_scene.level == "symbols"
    assert function_scene.owner_entity_id == "functions"
    assert scene_id("types", "functions") not in scenes
    assert {node.entity_id for node in function_scene.nodes if node.role == "node"} == {
        "functions.first",
        "functions.second",
    }

    types = scenes[scene_id("types", "model")]
    assert {node.entity_id for node in types.nodes if node.role == "node"} == {
        "model.Model",
        "model.helper",
    }
    members = scenes[scene_id("symbols", "model.Model")]
    assert all(node.role != "context" for scene in scenes.values() for node in scene.nodes)
    assert next(node for node in types.nodes if node.role == "frame").metadata["borderRadius"] == 0
    assert next(node for node in members.nodes if node.role == "frame").metadata["borderRadius"] == 12
    for scene in scenes.values():
        visuals = {node.visual_id for node in scene.nodes}
        assert all(
            edge.source_visual_id in visuals and edge.target_visual_id in visuals for edge in scene.edges
        )


def test_required_integration_fixture_semantics_and_provenance():
    graph = fixture_graph()
    scenes = build_scenes(graph, external=True)
    root = scenes["root"]
    assert {node.label for node in root.nodes if node.role == "group"} == {"p", "q"}
    assert {node.entity_id for node in root.nodes if node.role == "node"} == {
        "p.a",
        "p.b",
        "q.c",
    }
    assert root.edges[0].kinds == {"calls", "imports", "inherits"}
    assert root.edges[0].relationship_count == 4
    assert len(root.edges[0].provenance) == 4

    members = scenes[scene_id("symbols", "p.a.A")]
    assert {node.label for node in members.nodes if node.role == "node"} == {"run", "run.nested"}
    assert any(node.role == "context" and "p.b" in node.label for node in members.nodes)

    q_types = scenes[scene_id("types", "q.c")]
    assert any("TrèsLongUnicode" in node.label for node in q_types.nodes)
    assert any(node.role == "context" and "unresolved_target" in node.label for node in q_types.nodes)
    empty = scenes[scene_id("symbols", "q.c.Empty")]
    assert any(node.role == "empty" for node in empty.nodes)


def test_empty_scenes_initializer_groups_and_external_context_policy():
    graph = scan_sources(
        {
            "p/__init__.py": '"docs"',
            "p/empty.py": "",
            "p/model.py": "class Empty: pass\nclass Calls:\n def run(self): missing()\n",
        }
    )
    ordinary = build_scenes(graph)
    assert scene_id("types", "p.empty") in ordinary
    assert ordinary[scene_id("types", "p.empty")].nodes == []
    empty = ordinary[scene_id("symbols", "p.model.Empty")]
    assert empty.metadata["nestedClassCount"] == 0
    assert any(node.role == "empty" for node in empty.nodes)
    assert not any(node.role == "context" for node in ordinary[scene_id("symbols", "p.model.Calls")].nodes)
    package_nodes = [node for node in ordinary["root"].nodes if node.entity_id == "p"]
    assert len(package_nodes) == 1 and package_nodes[0].role == "group"

    with_external = build_scenes(graph, external=True)
    calls = with_external[scene_id("symbols", "p.model.Calls")]
    assert any(node.role == "context" and "missing" in node.label for node in calls.nodes)


def test_bucket_dimensions_are_deterministic():
    assert _dimensions(6, "bucketed") == (283, 142)
    assert _dimensions(0.1, "bucketed") == (174, 231)
    assert _dimensions(1.8, "square") == (200, 200)


def test_portal_camera_round_trip_with_negative_origin_and_letterboxing():
    embedding = portal_embedding((200, 120, 245, 164), (-20, -10, 900, 300))
    parent = (1.75, -81.5, 42.25)
    child = compose(parent, embedding)
    restored = inverse_rebase(child, embedding)
    assert restored == pytest.approx(parent)
    assert embedding[0] > 0


def test_manifest_rejects_dangling_edge():
    manifest = {
        "schemaVersion": 1,
        "rootSceneId": "root",
        "scenes": {
            "root": {
                "level": "modules",
                "viewBox": [0, 0, 1, 1],
                "nodes": [],
                "edges": [{"visualId": "edge", "sourceVisualId": "missing", "targetVisualId": "missing"}],
            }
        },
    }
    with pytest.raises(ValueError, match="dangling"):
        validate_manifest(manifest)


def test_svg_adapter_applies_supported_transforms_and_namespaces_resources():
    visual = "n_transform"
    encoded = base64.b64encode(visual.encode()).decode()
    scene = Scene("root", "modules", None, None, None, "Root")
    scene.nodes.append(SceneNode(visual, "pkg.mod", "module", "module"))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" data-d2-version="v0.9.0" viewBox="0 0 100 100">
      <svg width="100" height="100" viewBox="-10 -10 100 100">
        <defs><marker id="arrow"/></defs>
        <g class="{encoded}" transform="translate(5 6) scale(2)">
          <g class="shape"><rect x="1" y="2" width="3" height="4" marker-end="url(#arrow)"/></g>
        </g>
      </svg>
    </svg>'''.encode()
    normalized, _, nodes = adapt_svg(svg, scene)
    assert nodes[0]["bounds"] == pytest.approx([17, 20, 6, 8])
    root = ET.fromstring(normalized)
    semantic = next(element for element in root.iter() if element.get("data-visual-id") == visual)
    assert semantic.get("role") == "group"
    assert semantic.get("aria-label") == "module"
    assert any(element.get("id") == "root-arrow" for element in root.iter())
    assert any(element.get("marker-end") == "url(#root-arrow)" for element in root.iter())

    duplicate = Scene("s_duplicate", "types", "pkg.mod", "root", visual, "Duplicate")
    duplicate.nodes.append(SceneNode(visual, "pkg.mod", "module", "module"))
    other, _, _ = adapt_svg(svg, duplicate)
    other_root = ET.fromstring(other)
    assert any(element.get("id") == "s_duplicate-arrow" for element in other_root.iter())


def test_svg_adapter_rejects_active_content_and_nonuniform_geometry():
    scene = Scene("root", "modules", None, None, None, "Root")
    active = (
        b'<svg xmlns="http://www.w3.org/2000/svg" data-d2-version="v0.9.0" viewBox="0 0 1 1"><script/></svg>'
    )
    with pytest.raises(ValueError, match="active SVG"):
        adapt_svg(active, scene)

    visual = "n_bad"
    encoded = base64.b64encode(visual.encode()).decode()
    scene.nodes.append(SceneNode(visual, "bad", "bad", "module"))
    transformed = f'''<svg xmlns="http://www.w3.org/2000/svg" data-d2-version="v0.9.0" viewBox="0 0 10 10">
      <svg width="10" height="10" viewBox="0 0 10 10"><g class="{encoded}" transform="scale(2 3)"><rect width="1" height="1"/></g></svg>
    </svg>'''.encode()
    with pytest.raises(ValueError, match="nonuniform|Unsupported SVG transform"):
        adapt_svg(transformed, scene)


def test_html_cli_validation_and_default_name(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("class A: pass")
    assert main(["render", "--format", "html", "--level", "types"]) == 2
    assert "three-level hierarchy" in capsys.readouterr().err
    assert main(["render", "--html-node-shape", "square"]) == 2
    assert "only be used" in capsys.readouterr().err
    assert main(["render", "--html-view-mode", "isolated"]) == 2
    assert "only be used" in capsys.readouterr().err
    if shutil.which("d2"):
        assert main(["render", "--format", "html", "--layout", "tala"]) == 0
        assert (tmp_path / "archer/architecture-explorer.html").is_file()


def test_html_rejects_saved_diff_and_preserves_destination_on_failure(tmp_path, monkeypatch, capsys):
    before, after = scan_sources({"a.py": ""}), scan_sources({"a.py": "class A: pass"})
    path = tmp_path / "diff.json"
    path.write_text(diff(before, after).to_json())
    assert main(["render", "--root", str(tmp_path), "--graph", str(path), "--format", "html"]) == 2
    assert "saved diff" in capsys.readouterr().err

    from archer.render import html as html_render

    destination = tmp_path / "existing.html"
    destination.write_text("keep me")
    graph = scan_sources({"a.py": "class A: pass"})
    monkeypatch.setattr(html_render.shutil, "which", lambda _: "/fake/d2")
    monkeypatch.setattr(html_render, "_d2_version", lambda _: "0.9.0")
    monkeypatch.setattr(
        html_render,
        "_render_scene",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("scene failed")),
    )
    with pytest.raises(ValueError, match="scene failed"):
        html_render.render_html(graph, destination)
    assert destination.read_text() == "keep me"


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
def test_offline_html_contains_manifest_and_inert_scenes(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "hostile.py").write_text('class C: pass\nvalue = "</script>"')
    output = tmp_path / "architecture.html"
    assert (
        main(
            [
                "render",
                "--root",
                str(root),
                "--format",
                "html",
                "--layout",
                "tala",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    text = output.read_text()
    assert "fetch(" not in text and "<script src=" not in text and "https://" not in text
    match = re.search(r'<script id="archer-manifest" type="application/json">(.*?)</script>', text)
    manifest = json.loads(match.group(1))
    assert manifest["schemaVersion"] == 1
    assert len(manifest["scenes"]) == 3
    assert "\\u003c" in text


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
def test_html_stdout_is_only_the_complete_artifact(tmp_path, capsys):
    (tmp_path / "a.py").write_text("class A: pass")
    assert main(["render", "--root", str(tmp_path), "--format", "html", "--layout", "tala", "-o", "-"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("<!doctype html>") and captured.out.endswith("</html>")
    assert "Rendering scene" in captured.err


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
def test_incomplete_scan_writes_explorer_and_surfaces_diagnostics(tmp_path, capsys):
    (tmp_path / "broken.py").write_text("def unfinished(\n")
    output = tmp_path / "partial.html"
    assert (
        main(
            [
                "render",
                "--root",
                str(tmp_path),
                "--format",
                "html",
                "--layout",
                "tala",
                "-o",
                str(output),
            ]
        )
        == 2
    )
    text = output.read_text()
    manifest_match = re.search(r'<script id="archer-manifest" type="application/json">(.*?)</script>', text)
    manifest = json.loads(manifest_match.group(1))
    assert manifest["build"]["incompleteScan"] is True
    assert manifest["build"]["scanDiagnostics"]
    assert 'id="diagnostic-list"' in text
    assert "Incomplete scan" in capsys.readouterr().err


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
@pytest.mark.parametrize(
    ("layout", "shape", "optimization"),
    [
        ("tala", "bucketed", "raw"),
        ("elk", "bucketed", "medium"),
        ("dagre", "bucketed", "fast"),
        ("tala", "square", "fast"),
    ],
)
def test_html_actual_layout_shape_and_optimization_matrix(tmp_path, layout, shape, optimization):
    from archer.render.html import render_html

    output = tmp_path / f"{layout}-{shape}-{optimization}.html"
    report = render_html(
        fixture_graph(),
        output,
        layout=layout,
        node_shape=shape,
        svg_optimization=optimization,
        external=True,
    )
    text = output.read_text()
    match = re.search(r'<script id="archer-manifest" type="application/json">(.*?)</script>', text)
    manifest = json.loads(match.group(1))
    assert report["scenes"] == 1 + 3 + 5
    assert manifest["build"]["nodeShape"] == shape
    assert manifest["build"]["svgOptimization"] == optimization
    assert {scene["layout"] for scene in manifest["scenes"].values()} == {layout}
    assert all(0 < scene["letterboxOccupancy"] <= 1 for scene in manifest["scenes"].values())
    assert 'aria-label="' in text
    assert "data-archer-canvas" not in text


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
def test_isolated_square_html_uses_measured_frames_and_direct_symbol_scene(tmp_path):
    from archer.render.html import render_html

    graph = scan_sources(
        {
            "functions.py": "def first(): return second()\ndef second(): return 1\n",
            "model.py": "class Model:\n def run(self): return 1\ndef helper(): return 1\n",
        }
    )
    output = tmp_path / "isolated.html"
    render_html(graph, output, layout="tala", node_shape="square", view_mode="isolated")
    text = output.read_text()
    match = re.search(r'<script id="archer-manifest" type="application/json">(.*?)</script>', text)
    manifest = json.loads(match.group(1))
    scenes_match = re.search(r'<script id="archer-scenes" type="application/json">(.*?)</script>', text)
    scene_sources = json.loads(scenes_match.group(1))

    assert manifest["build"]["viewMode"] == "isolated"
    function_module = next(
        node for node in manifest["scenes"]["root"]["nodes"] if node["entityId"] == "functions"
    )
    assert manifest["scenes"][function_module["childSceneId"]]["level"] == "symbols"
    for scene_id_, scene in manifest["scenes"].items():
        if scene_id_ == "root":
            continue
        assert scene["frameBounds"][2] == pytest.approx(scene["frameBounds"][3])
        assert scene["contextNodeCount"] == 0
        parent = manifest["scenes"][scene["parentSceneId"]]
        card = next(node for node in parent["nodes"] if node["visualId"] == scene["parentVisualId"])
        assert card["bounds"][2] / card["bounds"][3] == pytest.approx(
            scene["frameBounds"][2] / scene["frameBounds"][3]
        )
        svg = ET.fromstring(scene_sources[scene_id_])
        canvas = next(element for element in svg.iter() if element.get("data-archer-canvas") == "true")
        assert canvas.get("fill") == "none"
        assert "fill:transparent" in canvas.get("style", "")
        assert any(
            rect.get("fill") == "white"
            for mask in svg.iter("{http://www.w3.org/2000/svg}mask")
            for rect in mask.iter("{http://www.w3.org/2000/svg}rect")
        ) or not list(svg.iter("{http://www.w3.org/2000/svg}mask"))
