"""Exercise the installed base wheel without importing the source checkout."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Hide compiler directories to verify that the installed package runs without Rust.
os.environ["PATH"] = os.pathsep.join(
    entry
    for entry in os.environ.get("PATH", "").split(os.pathsep)
    if not any((Path(entry) / name).exists() for name in ("cargo", "cargo.exe", "rustc", "rustc.exe"))
)
assert shutil.which("cargo") is None
assert shutil.which("rustc") is None

from archer import _native  # noqa: F401
from archer.scan import scan_sources

graph = scan_sources({"example.py": "def helper(): pass\ndef run(): helper()\n"}, parser="rust")
assert not graph.metadata["diagnostics"], graph.metadata["diagnostics"]
assert any(node.qualified_name == "example.helper" for node in graph.nodes.values())
assert any(edge.kind == "calls" for edge in graph.edges)

with tempfile.TemporaryDirectory() as directory:
    subprocess.run([sys.executable, "-I", "-m", "archer", "--version"], cwd=directory, check=True)
    result = subprocess.run(
        [sys.executable, "-I", "-m", "archer", "skill"],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Archer" in result.stdout

print("Installed wheel: native scan, CLI, and bundled skill passed without Rust on PATH.")
