import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from archer.cli import main
from archer.render import d2_source, render
from archer.scan import scan_sources


@pytest.fixture(autouse=True)
def isolate_cli_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_cli_roundtrip_and_saved_graph(tmp_path, capsys):
    (tmp_path / "a.py").write_text("def f(): pass\nf()\n")
    output = tmp_path / "graph.json"
    assert main(["scan", "--root", str(tmp_path), "-o", str(output)]) == 0
    assert json.loads(output.read_text())["schema_version"] == "1.0"
    assert main(["render", "--root", str(tmp_path), "--graph", str(output), "--format", "d2", "-o", "-"]) == 0
    assert "direction: right" in capsys.readouterr().out
    assert main(["context", "--root", str(tmp_path), "--focus", "a.f", "-o", "-"]) == 0
    assert "a.py" in capsys.readouterr().out
    assert main(["context", "--root", str(tmp_path), "--focus", "missing"]) == 2
    assert main(["check", "--root", str(tmp_path)]) == 0


def test_render_without_d2(tmp_path, monkeypatch):
    graph = scan_sources({"a.py": "unknown()"})
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert "unresolved" in d2_source(graph, "full")
    with pytest.raises(ValueError, match="--format d2"):
        render(graph, tmp_path / "a.svg")


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
def test_actual_d2_svg(tmp_path):
    graph = scan_sources({"a.py": "import b\ndef f(): missing()", "b.py": "x=1"})
    for level in ("modules", "types", "symbols", "full"):
        output = tmp_path / (level + ".svg")
        report = render(graph, output, level=level)
        assert report["layout"] in {"tala", "elk", "dagre"}
        assert ET.parse(output).getroot().tag.endswith("svg")


def test_command_entrypoint_and_skill():
    result = subprocess.run(
        [sys.executable, "-m", "archer", "doctor", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["archer"] == "0.1.1"
    result = subprocess.run(
        [sys.executable, "-m", "archer", "skill"], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0 and "name: archer" in result.stdout


def test_incomplete_scan_exit_and_config(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:")
    assert main(["scan", "--root", str(tmp_path)]) == 2
    (tmp_path / "pyproject.toml").write_text('[tool.archer]\nexclude=["bad.py"]\n')
    assert main(["scan", "--root", str(tmp_path)]) == 0


def test_schema_errors_and_output_extension(tmp_path):
    graph = tmp_path / "graph.json"
    graph.write_text('{"schema_version": "99"}')
    assert main(["context", "--root", str(tmp_path), "--graph", str(graph)]) == 2
    assert main(["render", "--root", str(tmp_path), "-o", str(tmp_path / "x.pdf")]) == 2


@pytest.mark.parametrize(
    "data", [[], {"schema_version": "1.0"}, {"schema_version": "1.0", "nodes": [{}], "edges": []}]
)
def test_malformed_graph_is_a_clean_cli_error(tmp_path, capsys, data):
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps(data))
    assert main(["context", "--root", str(tmp_path), "--graph", str(graph)]) == 2
    assert "Malformed Archer graph" in capsys.readouterr().err


def test_layout_fallback_and_explicit_failure(tmp_path, monkeypatch):
    graph = scan_sources({"a.py": "x=1"})
    engines = []
    monkeypatch.setattr(shutil, "which", lambda _: "/fake/d2")

    def fake_run(command, **kwargs):
        engines.append(command[2])
        if command[2] == "dagre":
            from pathlib import Path

            Path(command[-1]).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "layout unavailable")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = render(graph, tmp_path / "out.svg")
    assert engines == ["tala", "elk", "dagre"]
    assert result["layout"] == "dagre" and len(result["fallbacks"]) == 2
    with pytest.raises(ValueError, match="layout unavailable"):
        render(graph, tmp_path / "out.svg", layout="tala")


def test_check_reports_incomplete_scan_to_user(tmp_path, capsys):
    (tmp_path / "bad.py").write_text("def broken(:")
    assert main(["check", "--root", str(tmp_path)]) == 2
    assert "Incomplete scan" in capsys.readouterr().err


def test_nested_packages_focus_and_diff_colors():
    from archer.graph.algorithms import neighborhood, project
    from archer.graph.diff import diff
    from archer.render import COLORS, PALETTE, nested_nodes, subsystem_styles

    graph = scan_sources(
        {
            "pkg/models/__init__.py": "from .article import A",
            "pkg/models/article.py": "class A: pass",
            "pkg/service.py": "from .models import A\nA()",
        }
    )
    ids, _ = nested_nodes(project(graph), "modules")
    assert ids["pkg.models.article"].rsplit(".", 1)[0] == ids["pkg.models"].rsplit(".", 1)[0]
    assert ids["pkg.models.article"].count(".") == 2
    focused = neighborhood(graph, {"pkg.models.article"}, radius=0)
    assert "pkg.models" not in focused.nodes
    focused_ids, _ = nested_nodes(project(focused), "modules")
    assert focused_ids["pkg.models.article"] == ids["pkg.models.article"]
    assert subsystem_styles(focused) == subsystem_styles(graph)
    ordinary = d2_source(graph)
    assert any(fill in ordinary for _, fill, _ in PALETTE)
    delta = d2_source(diff(scan_sources({}), graph))
    assert COLORS["added"] in delta
    assert all(fill not in delta for _, fill, _ in PALETTE)


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
@pytest.mark.parametrize("layout", ["elk", "dagre", "tala"])
def test_nested_package_rendering_engines(tmp_path, layout):
    graph = scan_sources(
        {
            "p/__init__.py": "from .m import C",
            "p/m.py": "class C:\n def f(self): pass",
            "p/sub/use.py": "from p import C\nC()",
        }
    )
    for level in ("modules", "types", "full"):
        output = tmp_path / f"{layout}-{level}.svg"
        render(graph, output, level=level, layout=layout)
        assert ET.parse(output).getroot().tag.endswith("svg")


def test_default_artifact_paths_and_explicit_stdout(tmp_path, capsys):
    (tmp_path / "a.py").write_text("def f(): pass")
    assert main(["scan"]) == 0
    assert (tmp_path / "archer/graph.json").is_file()
    assert main(["render", "--format", "d2", "--level", "types"]) == 0
    assert (tmp_path / "archer/architecture-types.d2").is_file()
    assert main(["context", "--focus", "a.f", "--radius", "1"]) == 0
    assert (tmp_path / "archer/context-modules-focus-a.f-r1.md").is_file()
    capsys.readouterr()
    assert main(["scan", "-o", "-"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == "1.0"
    assert not (tmp_path / "-").exists()


def test_diff_filenames_from_saved_graph(tmp_path):
    from archer.graph.diff import diff

    graph = scan_sources({"a.py": ""})
    a, b = scan_sources({"a.py": "x=1"}), scan_sources({"a.py": "x=2"})
    a.metadata["snapshot"] = {"kind": "COMMIT", "revision": "a" * 40}
    b.metadata["snapshot"] = {"kind": "INDEX"}
    graph = diff(a, b)
    path = tmp_path / "input.json"
    path.write_text(graph.to_json())
    assert main(["scan", "--graph", str(path)]) == 0
    assert (tmp_path / "archer/graph-diff-staged.json").is_file()
    assert main(["render", "--graph", str(path), "--changes", "--format", "d2"]) == 0
    assert (tmp_path / "archer/architecture-changes-staged-modules-r2.d2").is_file()


def test_arrow_coloring_is_optional_and_diff_ignores_it():
    from archer.graph.diff import diff
    from archer.render import subsystem_styles

    graph = scan_sources({"pkg/a.py": "import pkg.b", "pkg/b.py": ""})
    plain, colored = d2_source(graph), d2_source(graph, color_arrows=True)
    assert plain != colored
    arrow = next(line for line in colored.splitlines() if " -> " in line)
    assert f'style.stroke: "{subsystem_styles(graph)["pkg.a"][2]}"' in colored.split(arrow)[1]
    delta = diff(scan_sources({}), graph)
    assert d2_source(delta) == d2_source(delta, color_arrows=True)


def test_arrow_exclusions_support_to_from_both_and_module_subtrees():
    from archer.graph.algorithms import project
    from archer.render import nested_nodes

    graph = scan_sources(
        {
            "pkg/models/__init__.py": "from .article import A",
            "pkg/models/article.py": "from pkg.store import save\nclass A: pass\nsave()",
            "pkg/service.py": "from pkg.models import A\nA()",
            "pkg/store.py": "def save(): pass",
        }
    )
    ids, _ = nested_nodes(project(graph), "modules")
    into_models = f"{ids['pkg.service']} -> {ids['pkg.models.article']}"
    from_models = f"{ids['pkg.models.article']} -> {ids['pkg.store']}"
    ordinary = d2_source(graph)
    assert into_models in ordinary and from_models in ordinary
    assert into_models not in d2_source(graph, exclude_arrows_to=("pkg.models",))
    assert from_models in d2_source(graph, exclude_arrows_to=("pkg.models",))
    assert from_models not in d2_source(graph, exclude_arrows_from=("pkg.models",))
    without_both = d2_source(graph, exclude_arrows=("pkg.models",))
    assert into_models not in without_both and from_models not in without_both


def test_cli_arrow_exclusions_and_default_filename(tmp_path, capsys):
    (tmp_path / "models.py").write_text("def make(): pass")
    (tmp_path / "service.py").write_text("from models import make\nmake()")
    assert main(["render", "--format", "d2", "--exclude-arrows-to", "models"]) == 0
    output = tmp_path / "archer/architecture-modules-without-arrows-to-models.d2"
    assert output.is_file() and " -> " not in output.read_text()
    assert main(["render", "--format", "d2", "--exclude-arrows", "missing"]) == 2
    assert "Unknown arrow-exclusion module" in capsys.readouterr().err


@pytest.mark.skipif(not shutil.which("d2"), reason="D2 integration runs in Docker")
def test_svg_postprocessing_and_raw_opt_out(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("from .b import f\ndef g(): f()")
    (tmp_path / "pkg/b.py").write_text("def f(): pass")
    assert main(["render", "--layout", "elk"]) == 0
    optimized = tmp_path / "archer/architecture-modules.svg"
    assert b"archer-svg-" in optimized.read_bytes()
    assert main(["render", "--layout", "elk", "--no-optimize-svg"]) == 0
    raw = tmp_path / "archer/architecture-modules-raw.svg"
    assert b"archer-svg-" not in raw.read_bytes()
    assert any(e.get("mask") for e in ET.parse(raw).iter())
    assert main(["render", "--layout", "elk", "--svg-optimization", "fast"]) == 0
    fast = tmp_path / "archer/architecture-modules-fast.svg"
    assert not any(e.get("mask") for e in ET.parse(fast).iter())
    assert main(["render", "--layout", "elk", "--svg-optimization", "raw"]) == 0
    assert b"archer-svg-" not in raw.read_bytes()


def test_environment_root_keeps_outputs_in_working_directory(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "environment.py").write_text("x=1")
    monkeypatch.setenv("ARCHER_ROOT", str(source))
    assert main(["scan"]) == 0
    output = tmp_path / "archer/graph.json"
    assert json.loads(output.read_text())["nodes"][0]["id"] == "environment"
    override = tmp_path / "override"
    override.mkdir()
    (override / "explicit.py").write_text("x=1")
    assert main(["scan", "--root", str(override)]) == 0
    assert json.loads(output.read_text())["nodes"][0]["id"] == "explicit"


def test_deep_file_reports_incomplete_scan(tmp_path, capsys):
    (tmp_path / "bad.py").write_text("x = " + "+".join(["x"] * 600))
    (tmp_path / "ok.py").write_text("def good(): pass")
    assert main(["scan", "--root", str(tmp_path), "-o", "-"]) == 2
    result = capsys.readouterr()
    graph = json.loads(result.out)
    assert "ok.good" in {n["id"] for n in graph["nodes"]}
    assert graph["metadata"]["diagnostics"][0]["stage"] == "complexity"
    assert "Incomplete scan" in result.err


def test_deep_saved_graph_is_a_clean_error(tmp_path, capsys):
    graph = tmp_path / "deep.json"
    graph.write_text("[" * 10000 + "0" + "]" * 10000)
    assert main(["scan", "--root", str(tmp_path), "--graph", str(graph)]) == 2
    assert "Graph JSON exceeds supported nesting depth" in capsys.readouterr().err


@pytest.mark.parametrize("invalid", ['"invalid"', "[]", "true"])
def test_parser_flag_overrides_configuration(tmp_path, capsys, invalid):
    (tmp_path / "a.py").write_text("def f(): pass\nf()")
    config = tmp_path / "pyproject.toml"
    config.write_text(f"[tool.archer]\nparser={invalid}\n")
    assert main(["scan", "--root", str(tmp_path), "-o", "-"]) == 2
    assert "parser must be" in capsys.readouterr().err
    for backend in ("rust", "libcst"):
        assert main(["scan", "--root", str(tmp_path), "--parser", backend, "-o", "-"]) == 0
        graph = json.loads(capsys.readouterr().out)
        assert "a.f" in {n["id"] for n in graph["nodes"]}
        config.write_text(f'[tool.archer]\nparser="{backend}"\n')
        assert main(["scan", "--root", str(tmp_path), "-o", "-"]) == 0
        assert json.loads(capsys.readouterr().out) == graph
