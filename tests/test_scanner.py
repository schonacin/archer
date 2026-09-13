import json

from archer.graph.model import Graph
from archer.scan import scan, scan_sources


def edges(graph, kind):
    return {(e.source, e.target): e for e in graph.edges if e.kind == kind}


def test_entities_calls_imports_inheritance_and_reexports():
    graph = scan_sources(
        {
            "src/pkg/__init__.py": "from .base import Base\n",
            "src/pkg/base.py": "class Base:\n def run(self): pass\n",
            "src/pkg/use.py": "from pkg import Base as B\nclass Child(B):\n def work(self):\n  self.run()\n  missing()\ndef outer():\n def inner(): pass\n inner()\n",
        }
    )
    assert graph.nodes["pkg"].kind == "package"
    assert graph.nodes["pkg.use.Child.work"].kind == "method"
    assert graph.nodes["pkg.use.outer.inner"].kind == "function"
    assert ("pkg.use.Child", "pkg.base.Base") in edges(graph, "inherits")
    calls = edges(graph, "calls")
    assert calls["pkg.use.Child.work", "pkg.base.Base.run"].resolution["status"] == "strong"
    assert calls["pkg.use.outer", "pkg.use.outer.inner"].resolution["status"] == "exact"
    assert any(e.resolution["status"] == "unresolved" for e in calls.values())
    assert Graph.from_dict(json.loads(graph.to_json())).to_json() == graph.to_json()


def test_parameter_shadowing_not_resolved_to_global():
    graph = scan_sources({"a.py": "def target(): pass\ndef caller(target): target()\n"})
    calls = edges(graph, "calls")
    assert ("a.caller", "a.target") not in calls
    assert next(iter(calls.values())).resolution["status"] == "unresolved"


def test_dynamic_and_ambiguous_calls_remain_visible():
    graph = scan_sources(
        {"a.py": "from x import f\nif enabled:\n from y import f\ndef run():\n f()\n registry[name]()\n"}
    )
    calls = list(edges(graph, "calls").values())
    assert len(calls) == 2
    assert {e.resolution["status"] for e in calls} == {"ambiguous", "unresolved"}


def test_line_stability_and_aggregated_sites():
    source = "def f(): pass\ndef g():\n f()\n f()\n"
    a = scan_sources({"a.py": source})
    b = scan_sources({"a.py": "\n# heading\n" + source})
    assert set(a.nodes) == set(b.nodes)
    assert a.nodes["a.g"].metadata["fingerprint"] == b.nodes["a.g"].metadata["fingerprint"]
    assert len(edges(a, "calls")["a.g", "a.f"].metadata["sites"]) == 2


def test_relative_import_and_namespace_packages():
    graph = scan_sources({"src/p/sub/a.py": "from ..b import f\nf()", "src/p/b.py": "def f(): pass"})
    assert graph.nodes["p"].metadata["namespace"]
    assert ("p.sub.a", "p.b.f") in edges(graph, "calls")


def test_parse_errors_are_reported_without_hiding_other_files():
    graph = scan_sources({"bad.py": "def broken(:", "ok.py": "def good(): pass"})
    assert graph.metadata["diagnostics"][0]["file"] == "bad.py"
    assert "ok.good" in graph.nodes
    assert "bad" in graph.nodes


def test_encoding_exclusions_and_symlinks(tmp_path):
    (tmp_path / "a.py").write_bytes(b"# coding: latin-1\nname = '\xe9'\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "a.py")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "bad.py").write_text("def broken(:")
    graph = scan(tmp_path)
    assert graph.metadata["modules"] == ["a"]
    assert not graph.metadata["diagnostics"]
    assert not scan(tmp_path, excludes=["a.py"]).nodes


def test_attribute_constructor_inference():
    graph = scan_sources(
        {
            "a.py": "class Worker:\n def run(self): pass\nclass App:\n def __init__(self):\n  self.worker = Worker()\n def run(self):\n  self.worker.run()\n"
        }
    )
    assert edges(graph, "calls")["a.App.run", "a.Worker.run"].resolution["status"] == "inferred"


def test_repeated_definitions_ambiguous():
    graph = scan_sources({"a.py": "if flag:\n def f(): pass\nelse:\n def f(): pass\nf()"})
    assert {"a.f", "a.f#2"} <= graph.nodes.keys()
    assert all(e.resolution["status"] == "ambiguous" for e in edges(graph, "calls").values())


def test_constructor_injected_dependency_and_parameter_calls():
    graph = scan_sources(
        {
            "a.py": "class Pipeline:\n def process(self): pass\nclass App:\n def __init__(self, pipeline: Pipeline):\n  self.pipeline = pipeline\n def run(self):\n  self.pipeline.process()\ndef use(p: Pipeline): p.process()\n"
        }
    )
    calls = edges(graph, "calls")
    assert calls["a.App.run", "a.Pipeline.process"].resolution["status"] == "inferred"
    assert calls["a.use", "a.Pipeline.process"].resolution["status"] == "inferred"


def test_nested_self_shadow_does_not_bind_outer_class():
    graph = scan_sources(
        {"a.py": "class C:\n def run(self): pass\n def f(self):\n  def nested(self): self.run()\n"}
    )
    calls = edges(graph, "calls")
    assert ("a.C.f.nested", "a.C.run") not in calls


def test_conditional_reexports_remain_ambiguous():
    graph = scan_sources(
        {
            "p/__init__.py": "if flag:\n from .a import f\nelse:\n from .b import f\n",
            "p/a.py": "def f(): pass",
            "p/b.py": "def f(): pass",
            "use.py": "from p import f\nf()",
        }
    )
    calls = edges(graph, "calls")
    assert calls["use", "p.a.f"].resolution["status"] == "ambiguous"
    assert calls["use", "p.b.f"].resolution["status"] == "ambiguous"


def test_reassigned_function_is_not_exact():
    graph = scan_sources({"a.py": "def f(): pass\nf = other\nf()"})
    assert all(e.resolution["status"] != "exact" for e in edges(graph, "calls").values())


def test_indentation_changes_are_semantic():
    from archer.graph.diff import diff

    a = scan_sources({"a.py": "if flag:\n f()\n g()\n"})
    b = scan_sources({"a.py": "if flag:\n f()\ng()\n"})
    assert diff(a, b).nodes["a"].metadata["change"] == "modified"


def test_staticmethod_and_reassigned_receiver_are_not_claimed_strong():
    graph = scan_sources(
        {
            "a.py": "class C:\n def run(self): pass\n @staticmethod\n def f(self): self.run()\n def g(self):\n  self = other\n  self.run()\n"
        }
    )
    assert all(e.resolution["status"] == "unresolved" for e in edges(graph, "calls").values())


def test_class_annotated_attributes():
    graph = scan_sources(
        {
            "a.py": "class Worker:\n def run(self): pass\nclass C:\n worker: Worker\n def go(self): self.worker.run()\n"
        }
    )
    assert edges(graph, "calls")["a.C.go", "a.Worker.run"].resolution["status"] == "inferred"


def test_module_source_ranges_have_real_end_positions():
    for source, end in [
        ("x=1", {"line": 1, "column": 3}),
        ("x=1\n", {"line": 2, "column": 0}),
        ("", {"line": 1, "column": 0}),
    ]:
        assert scan_sources({"a.py": source}).nodes["a"].source_range["end"] == end
