"""Canonical IR is the compatibility contract, including hashes and source ranges."""

import pytest

from archer.scan import scan_sources

SOURCES = [
    "def f(): pass\ndef g():\n f()\n f()\n",
    "from x import f\nif enabled:\n from y import f\ndef run():\n f()\n registry[name]()\n",
    "class Worker:\n def run(self): pass\nclass App:\n def __init__(self):\n  self.worker = Worker()\n def run(self):\n  self.worker.run()\n",
    "class C:\n def run(self): pass\n @staticmethod\n def f(self): self.run()\n def g(self):\n  self = other\n  self.run()\n",
    "from x import A\ndef f(a: A): a.run()\n",
    "f()\ndef f(): pass\nf()\n",
    "from x import f\nf()\nfrom y import f\nf()\n",
    "if flag:\n def f(): pass\nelse:\n def f(): pass\nf()\n",
    "def outer():\n def inner(): pass\n inner()\n",
    "class C:\n def f(self):\n  def nested(self): self.run()\n",
    'def café(): pass\nπ = "é"; café()\n',
    "import a.b\nimport a.b as c\na.b.f()\nc.f()\n",
    "@decorator(factory())\nasync def f(a=default()):\n await a()\n",
    'class C:\n @decorator\n def f(self):\n  return """first\n  keep this indentation\n  """\n',
    "def f(): pass\n(f)()\n((f))()\n",
    "from somewhere import *\nunknown()\n",
    "[x() for x in fs]\nf = lambda x: x()\n",
    "class C(Base[T], metaclass=Meta): pass\n",
    "def f():\n global x\n x = C()\n x.run()\n",
    "from x import f\nf = f()\nf()",
    "def f():\n x=x()\n x()",
    "def f(): pass\nclass C:\n f()\n f=other",
    "def outer():\n def inner():\n  nonlocal x\n  x()\n x = other\n inner()",
    "def f():\n [x() for x in x()]",
    "def f():\n a=lambda x:x()\n a()",
    "try:\n f()\nexcept Error as e:\n e.run()",
    'match subject:\n case {"x":x}: x()',
    "from x import A\nclass C:\n A=other\n def f(self, x:A): x.run()",
    "def f[T](x:T):\n x.run()",
    "from x import A\ntype T = A\ndef f(x:T): x.run()",
    "import x\n(\n x()\n .f\n)()",
    'def f():\n return f"""a\n    b{x}\n    c""".encode()',
    "def f():\n with factory() as x:\n  x.run()",
    "def f():\n for x in x():\n  x.run()",
    "def café(): pass\r\ncafé()\r\n",
    "class C:\n def f(self):\n  return [self.x() for self in xs]",
    "if x:\n def f(): pass; other()\n",
]


@pytest.mark.parametrize("source", SOURCES)
def test_identical_ir(source):
    rust = scan_sources({"a.py": source}, parser="rust")
    libcst = scan_sources({"a.py": source}, parser="libcst")
    assert not rust.metadata["diagnostics"]
    assert rust.to_dict() == libcst.to_dict()


def test_unknown_backend():
    with pytest.raises(ValueError, match="Unknown parser"):
        scan_sources({}, parser="missing")


def test_repository_parity():
    from pathlib import Path

    from archer.scan import read_sources

    root = Path(__file__).resolve().parents[1]
    sources = read_sources(root / "src")
    rust = scan_sources(sources, parser="rust")
    libcst = scan_sources(sources, parser="libcst")
    assert not rust.metadata["diagnostics"]
    assert not libcst.metadata["diagnostics"]
    assert rust.to_dict() == libcst.to_dict()


def test_rust_does_not_import_libcst():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from archer.scan import scan_sources; import sys; "
                "scan_sources({'a.py':'def f(): pass'}); "
                "assert not any(m.startswith('libcst') for m in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_native_limits_in_subprocess():
    import subprocess
    import sys

    code = """
from archer.scan import scan_sources
for source in ['x=' + '+'.join(['x']*20000), 'x=' + '('*10000 + '1' + ')'*10000]:
    graph = scan_sources({'bad.py': source, 'ok.py':'def good(): pass'}, parser='rust')
    assert 'ok.good' in graph.nodes
    assert graph.metadata['diagnostics'][0]['stage'] == 'complexity'
"""
    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_backend_missing_is_explicit(monkeypatch):
    from archer.scan import parser

    def missing(name):
        raise ImportError("injected missing dependency")

    monkeypatch.setattr(parser, "import_module", missing)
    with pytest.raises(ValueError, match="--parser libcst"):
        scan_sources({})
    with pytest.raises(ValueError, match=r"archer\[libcst\]"):
        scan_sources({}, parser="libcst")


@pytest.mark.parametrize("backend", ["rust", "libcst"])
def test_saved_graph_compatibility(backend):
    import json
    import sys
    from pathlib import Path

    from archer.graph.diff import diff
    from archer.graph.model import Graph

    if sys.version_info[:2] != (3, 12):
        pytest.skip("Legacy fixture uses Python 3.12 token numbers; other versions run live parity tests")
    fixtures = Path(__file__).parent / "fixtures"
    old = Graph.from_dict(json.loads((fixtures / "legacy_graph.json").read_text()))
    sources = json.loads((fixtures / "legacy_sources.json").read_text())
    new = scan_sources(sources, parser=backend)
    delta = diff(old, new)
    assert all(n.metadata["change"] == "unchanged" for n in delta.nodes.values())
    assert all(e.metadata["change"] == "unchanged" for e in delta.edges)
