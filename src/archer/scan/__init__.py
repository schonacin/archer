"""Scan filesystem or immutable in-memory snapshots without importing project code."""

import ast
import fnmatch
import hashlib
import io
import os
import tokenize
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from archer.graph.model import Edge, Graph, Node, resolution
from archer.scan.cache import FactsCache, backend_identity, cache_key
from archer.scan.facts import direct_fingerprints
from archer.scan.parser import ExtractionLimitError, fingerprint, get_parser
from archer.scan.resolver import Resolver

EXCLUDED = {".git", ".venv", "venv", "env", "__pycache__", "node_modules", "build", "dist", ".tox", ".archer"}


def initializer_is_structural(source):
    """Conservatively recognize only docstrings and imports."""
    try:
        body = ast.parse(source).body
    except (SyntaxError, RecursionError, ValueError):
        return False
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return all(isinstance(statement, (ast.Import, ast.ImportFrom)) for statement in body)


def ignore_spec(root):
    path = Path(root) / ".archerignore"
    return GitIgnoreSpec.from_lines(path.read_text(encoding="utf-8").splitlines() if path.is_file() else [])


def included(path, excludes=(), ignore=None):
    parts = PurePosixPath(path).parts
    return (
        path.endswith(".py")
        and not any(p in EXCLUDED or p.startswith(".") for p in parts)
        and not any(fnmatch.fnmatch(path, pattern) for pattern in excludes)
        and not (ignore and ignore.match_file(path))
    )


def read_sources(root, excludes=()):
    root = Path(root).resolve()
    ignore = ignore_spec(root)
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in EXCLUDED and not d.startswith(".") and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(files):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if included(rel, excludes, ignore) and not path.is_symlink():
                result[rel] = path.read_bytes()
    return result


def module_name(path, roots):
    p = PurePosixPath(path)
    for root in sorted(roots, key=len, reverse=True):
        try:
            p = p.relative_to(root)
            break
        except ValueError:
            pass
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__root__"


def scan_sources(
    sources,
    *,
    source_roots=None,
    excludes=(),
    snapshot=None,
    parser="rust",
    cache=True,
    cache_dir=None,
    cache_max_mb=512,
):
    if not isinstance(cache, bool):
        raise TypeError("cache must be a boolean")
    if type(cache_max_mb) is not int or cache_max_mb < 0:
        raise ValueError("cache_max_mb must be a nonnegative integer")
    extract = get_parser(parser)
    sources = {p: s for p, s in sources.items() if included(p, excludes)}
    roots = (
        source_roots
        if source_roots is not None
        else (["src"] if any(p.startswith("src/") for p in sources) else ["."])
    )
    graph = Graph(
        metadata={
            "snapshot": snapshot or {"kind": "WORKTREE"},
            "source_roots": roots,
            "diagnostics": [],
            "modules": [],
        }
    )
    visitors = []
    fact_cache = None
    identity = None
    if cache and cache_max_mb:
        try:
            identity = backend_identity(parser)
            fact_cache = FactsCache(cache_dir, cache_max_mb * 1024 * 1024)
        except (OSError, ImportError):
            pass
    try:
        for file, raw in sorted(sources.items()):
            module = module_name(file, roots)
            if module in graph.nodes:
                raise ValueError(
                    f"Module collision for {module}: {graph.nodes[module].file} and {file}; configure --source-root"
                )
            try:
                if isinstance(raw, bytes):
                    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                    source = raw.decode(encoding)
                else:
                    source = raw
                key = cache_key(raw, module, file, identity) if fact_cache is not None else None
                cached = fact_cache.get(key, module, file) if fact_cache is not None else None
                if cached is None:
                    facts = extract(source, module, file)
                    digest = fingerprint(source)
                    facts.direct_fingerprints = direct_fingerprints(source, module)
                    if fact_cache is not None:
                        fact_cache.put(key, facts, digest)
                else:
                    facts, digest = cached
                lines = source.splitlines()
                trailing_newline = source.endswith(("\n", "\r"))
                end_line = max(1, len(lines) + int(trailing_newline))
                end_column = 0 if trailing_newline or not lines else len(lines[-1])
                node = Node(
                    module,
                    "package" if file.endswith("__init__.py") else "module",
                    module,
                    module,
                    file,
                    {
                        "start": {"line": 1, "column": 0},
                        "end": {"line": end_line, "column": end_column},
                    },
                    {"fingerprint": digest},
                )
                if node.kind == "package":
                    node.metadata["structural_initializer"] = initializer_is_structural(source)
                for declaration in (node, *facts.nodes.values()):
                    if direct := facts.direct_fingerprints.get(declaration.id):
                        declaration.metadata["direct_fingerprint"] = direct
                graph.nodes[module] = node
                graph.nodes.update(facts.nodes)
                graph.edges.extend(facts.edges)
                visitors.append(facts)
            except (
                ExtractionLimitError,
                RecursionError,
                tokenize.TokenError,
                IndentationError,
                SyntaxError,
                UnicodeError,
            ) as exc:
                # Preserve the module and report an incomplete scan explicitly.
                graph.nodes[module] = Node(
                    module,
                    "package" if file.endswith("__init__.py") else "module",
                    module,
                    module,
                    file,
                    None,
                    {
                        "parse_error": str(exc),
                        "fingerprint": hashlib.sha256(
                            raw if isinstance(raw, bytes) else raw.encode()
                        ).hexdigest(),
                    },
                )
                graph.metadata["diagnostics"].append(
                    {
                        "file": file,
                        "severity": "error",
                        "stage": getattr(exc, "stage", "parse"),
                        "message": str(exc),
                    }
                )
            graph.metadata["modules"].append(module)
    finally:
        if fact_cache is not None:
            fact_cache.close()
    # Namespace packages have no source file but remain part of the hierarchy.
    for module in list(graph.metadata["modules"]):
        parts = module.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            graph.nodes.setdefault(
                parent, Node(parent, "package", parent, parent, None, None, {"namespace": True})
            )
    graph.metadata["modules"] = sorted(n.id for n in graph.nodes.values() if n.kind in {"module", "package"})
    for module in graph.metadata["modules"]:
        parent = module.rpartition(".")[0]
        if parent in graph.nodes:
            graph.add_edge(Edge(parent, module, "contains", None, resolution()))
    return Resolver(graph, visitors).run()


def scan(root=".", **kwargs):
    return scan_sources(read_sources(root, kwargs.get("excludes", ())), **kwargs)
