from archer.checks import check
from archer.context import context
from archer.graph.algorithms import changes, neighborhood, project, reachable
from archer.graph.diff import diff
from archer.scan import scan_sources


def test_diff_semantics_deleted_nodes_and_modified_edges():
    before = scan_sources({"a.py": "def f(): pass\ndef g(): f()\n", "gone.py": "import a"})
    after = scan_sources({"a.py": "\n# comment\ndef f(): pass\ndef g(): f()\n"})
    delta = diff(before, after)
    assert delta.nodes["a.g"].metadata["change"] == "unchanged"
    assert delta.nodes["gone"].metadata["change"] == "removed"
    assert any(e.metadata["change"] == "removed" for e in delta.edges)
    changed = diff(before, scan_sources({"a.py": "def f(): return 3\ndef g(): f()\n"}))
    assert changed.nodes["a.f"].metadata["change"] == "modified"
    assert changed.nodes["a.f"].metadata["before"]["file"] == "a.py"


def test_neighborhood_uses_architecture_distance_not_containment():
    before = scan_sources({"a.py": "import b", "b.py": "import c", "c.py": "x=1", "z.py": "x=1"})
    after = scan_sources({"a.py": "import b", "b.py": "import c", "c.py": "x=2", "z.py": "x=1"})
    delta = diff(before, after)
    assert set(changes(delta, 0).nodes) == {"c"}
    assert set(changes(delta, 1).nodes) == {"b", "c"}
    assert set(changes(delta, 2).nodes) == {"a", "b", "c"}
    assert set(neighborhood(delta, {"c"}, 2, "outgoing").nodes) == {"c"}
    assert reachable(before, "a") == ["b", "c"]


def test_deleted_module_is_a_change_seed():
    a = scan_sources({"a.py": "import b", "b.py": "x=1"})
    b = scan_sources({"a.py": "x=1"})
    graph = changes(diff(a, b), 0)
    assert graph.nodes["b"].metadata["change"] == "removed"


def test_views_aggregate_dependencies_and_keep_full():
    graph = scan_sources({"a.py": "from b import f\ndef g(): f()", "b.py": "def f(): pass"})
    view = project(graph)
    assert set(view.nodes) == {"a", "b"}
    assert ("a", "b", "calls") in {e.key for e in view.edges}
    assert set(project(graph, "full").nodes) == set(graph.nodes)


def test_checks_new_growing_scc_fans_and_coupling():
    baseline = scan_sources({"a.py": "import b", "b.py": "import a", "c.py": ""})
    current = scan_sources({"a.py": "import b\nimport c", "b.py": "import a", "c.py": "import a"})
    rules = {f["rule"] for f in check(current, baseline, fan_threshold=1)["findings"]}
    assert {"dependency-cycle", "growing-scc", "high-fan-in", "high-fan-out", "coupling-increase"} <= rules
    assert "new-scc" in {
        f["rule"] for f in check(baseline, scan_sources({"a.py": "", "b.py": ""}))["findings"]
    }


def test_context_budget_and_change_priority():
    before = scan_sources({"a.py": "", "b.py": ""})
    after = scan_sources({"a.py": "import b", "b.py": ""})
    output = context(diff(before, after))
    assert "added: a --imports--> b" in output
    assert "a.py:1" in output
    assert len(context(after, max_chars=200)) <= 200


def test_unresolved_formatting_does_not_create_architecture_changes():
    a = scan_sources({"a.py": "registry[key]()\n"})
    b = scan_sources({"a.py": "registry[ key ]()\n"})
    delta = diff(a, b)
    assert all(n.metadata["change"] == "unchanged" for n in delta.nodes.values())
    assert all(e.metadata["change"] == "unchanged" for e in delta.edges)


def test_resolution_change_classifies_edge_modified():
    a = scan_sources({"a.py": "def f(): pass\nf()\n"})
    b = scan_sources({"a.py": "def f(): pass\nf = other\nf()\n"})
    edge = next(e for e in diff(a, b).edges if e.kind == "calls")
    assert edge.metadata["change"] == "modified"
    assert edge.metadata["before"]["resolution"]["status"] == "exact"


def test_direct_changes_do_not_charge_owners_for_descendant_bodies():
    before = scan_sources({"p/m.py": "class C:\n def f(self): return 1\ndef g(): return 1"})
    after = scan_sources({"p/m.py": "class C:\n def f(self): return 2\ndef g(): return 2"})
    delta = diff(before, after)
    assert delta.nodes["p.m"].metadata["change"] == "modified"
    assert delta.nodes["p.m"].metadata["direct_change"] == "unchanged"
    assert delta.nodes["p.m.C"].metadata["direct_change"] == "unchanged"
    assert delta.nodes["p.m.C.f"].metadata["direct_change"] == "modified"
    assert delta.nodes["p.m.g"].metadata["direct_change"] == "modified"
    summary = [
        {"kind": "function", "change": "modified", "count": 1},
        {"kind": "method", "change": "modified", "count": 1},
    ]
    assert delta.nodes["p.m"].metadata["descendant_changes"] == summary
    assert delta.nodes["p"].metadata["descendant_changes"] == summary
    assert project(changes(delta, 0), "modules").nodes["p.m"].metadata["descendant_changes"] == summary


def test_direct_body_imports_and_definition_headers():

    # Each edit must remain attributed even if there are also child changes.
    for old, new, owner in [
        ("x = 1\ndef f(): return 1", "x = 2\ndef f(): return 2", "m"),
        ("import a\ndef f(): pass", "import b\ndef f(): pass", "m"),
        ("class C(A):\n def f(self): return 1", "class C(B):\n def f(self): return 2", "m.C"),
        ("def f(a=1): return 1", "def f(a=2): return 1", "m.f"),
        ("@a\ndef f(): pass", "@b\ndef f(): pass", "m.f"),
        ("async def f(): return 1", "async def f(): return 2", "m.f"),
    ]:
        delta = diff(scan_sources({"m.py": old}), scan_sources({"m.py": new}))
        assert delta.nodes[owner].metadata["direct_change"] == "modified"
        if owner != "m":
            assert delta.nodes["m"].metadata["direct_change"] == "unchanged"


def test_added_removed_nested_symbols_count_without_modifying_parent_body():
    old = "def outer():\n def gone(): return 1\n return 0"
    new = "def outer():\n def added(): return 2\n return 0"
    delta = diff(scan_sources({"m.py": old}), scan_sources({"m.py": new}))
    assert delta.nodes["m"].metadata["direct_change"] == "unchanged"
    assert delta.nodes["m.outer"].metadata["direct_change"] == "unchanged"
    assert delta.nodes["m.outer"].metadata["descendant_changes"] == [
        {"kind": "function", "change": "added", "count": 1},
        {"kind": "function", "change": "removed", "count": 1},
    ]


def test_missing_attribution_is_unknown_without_false_legacy_changes():
    old = scan_sources({"m.py": "def f(): return 1"})
    current = scan_sources({"m.py": "def f(): return 1"})
    for node in old.nodes.values():
        node.metadata.pop("direct_fingerprint", None)
    assert all(n.metadata["change"] == "unchanged" for n in diff(old, current).nodes.values())
    changed = diff(old, scan_sources({"m.py": "def f(): return 2"}))
    assert changed.nodes["m"].metadata["direct_change"] == "unknown"
    assert changed.nodes["m.f"].metadata["direct_change"] == "unknown"
