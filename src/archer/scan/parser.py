"""LibCST extraction with positions and lexical qualified-name metadata."""

import hashlib
import io
import tokenize
from collections import defaultdict

import libcst as cst
from libcst.helpers import get_full_name_for_node
from libcst.metadata import MetadataWrapper, PositionProvider, QualifiedNameProvider, QualifiedNameSource

from archer.graph.model import Edge, Node, resolution


def fingerprint(code):
    """Ignore comments/formatting, but retain literals and executable tokens."""
    tokens = [
        (t.type, "" if t.type == tokenize.INDENT else t.string)
        for t in tokenize.generate_tokens(io.StringIO(code).readline)
        if t.type
        not in {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING, tokenize.ENDMARKER}
    ]
    return hashlib.sha256(repr(tokens).encode()).hexdigest()


class Extractor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider, QualifiedNameProvider)

    def __init__(self, graph, module, file, tree):
        self.graph, self.module, self.file, self.tree = graph, module, file, tree
        self.stack = [module]
        self.kinds = ["module"]
        self.counts = defaultdict(int)
        self.pending = []
        self.exports = {}
        self.attributes = {}
        self.bindings = {}
        self.rebindings = set()
        self.receivers = {}

    def location(self, node):
        pos = self.get_metadata(PositionProvider, node)
        return {
            "start": {"line": pos.start.line, "column": pos.start.column},
            "end": {"line": pos.end.line, "column": pos.end.column},
        }

    def names(self, node):
        result = []
        for q in self.get_metadata(QualifiedNameProvider, node, set()):
            if q.source == QualifiedNameSource.LOCAL:
                result.append(self.module + "." + q.name.replace(".<locals>.", "."))
            elif q.source == QualifiedNameSource.IMPORT:
                result.append(self.absolute(q.name))
            else:
                result.append(q.name)
        return sorted(set(result))

    def absolute(self, name):
        if not name.startswith("."):
            return name
        level = len(name) - len(name.lstrip("."))
        package = (
            self.module
            if self.file.endswith("/__init__.py") or self.file == "__init__.py"
            else self.module.rpartition(".")[0]
        )
        parts = package.split(".") if package else []
        base = parts[: len(parts) - level + 1] if level <= len(parts) else []
        return ".".join(base + ([name[level:]] if name[level:] else []))

    def declare(self, node, kind):
        name = self.stack[-1] + "." + node.name.value
        self.counts[name] += 1
        ident = name if self.counts[name] == 1 else f"{name}#{self.counts[name]}"
        self.graph.nodes[ident] = Node(
            ident,
            kind,
            name,
            self.module,
            self.file,
            self.location(node),
            {"fingerprint": fingerprint(self.tree.code_for_node(node))},
        )
        self.graph.add_edge(Edge(self.stack[-1], ident, "contains", self.location(node), resolution()))
        self.stack.append(ident)
        self.kinds.append(kind)

    def visit_ClassDef(self, node):
        self.declare(node, "class")
        for base in node.bases:
            value = base.value.value if isinstance(base.value, cst.Subscript) else base.value
            self.reference(value, "inherits", source=self.stack[-1])

    def leave_ClassDef(self, node):
        self.stack.pop()
        self.kinds.pop()

    def visit_FunctionDef(self, node):
        self.declare(node, "method" if self.kinds[-1] == "class" else "function")
        decorators = {get_full_name_for_node(d.decorator) for d in node.decorators}
        params = list(node.params.posonly_params) + list(node.params.params)
        if self.kinds[-1] == "method" and "staticmethod" not in decorators and params:
            for name in self.names(params[0].name):
                self.receivers[name] = self.stack[-2]

    def leave_FunctionDef(self, node):
        self.stack.pop()
        self.kinds.pop()

    def reference(self, node, kind, source=None, candidates=None, hint=None):
        self.pending.append(
            {
                "source": source or self.stack[-1],
                "kind": kind,
                "text": self.tree.code_for_node(node),
                "identity": fingerprint(self.tree.code_for_node(node)),
                "range": self.location(node),
                "candidates": self.names(node) if candidates is None else candidates,
                "hint": hint,
                "receivers": dict(self.receivers),
            }
        )

    def visit_Call(self, node):
        self.reference(node.func, "calls")

    def visit_Import(self, node):
        for alias in node.names:
            name = get_full_name_for_node(alias.name)
            self.reference(alias.name, "imports", candidates=[name])
            if len(self.stack) == 1:
                local = alias.asname.name.value if alias.asname else name.split(".")[0]
                self.exports.setdefault(self.module + "." + local, set()).add(
                    name if alias.asname else name.split(".")[0]
                )

    def visit_ImportFrom(self, node):
        prefix = self.absolute("." * len(node.relative) + (get_full_name_for_node(node.module) or ""))
        if isinstance(node.names, cst.ImportStar):
            self.reference(
                node, "imports", candidates=[prefix], hint="wildcard import; exported names unknown"
            )
            return
        for alias in node.names:
            name = get_full_name_for_node(alias.name)
            target = prefix + "." + name if prefix else name
            self.reference(alias.name, "imports", candidates=[target])
            if len(self.stack) == 1:
                local = alias.asname.name.value if alias.asname else name
                self.exports.setdefault(self.module + "." + local, set()).add(target)

    def visit_Param(self, node):
        if node.annotation:
            for name in self.names(node.name):
                self.bindings.setdefault(name, set()).update(self.names(node.annotation.annotation))

    def assignment(self, target, types):
        for name in self.names(target):
            self.rebindings.add(name)
            self.bindings.setdefault(name, set()).update(types)
        if (
            isinstance(target, cst.Attribute)
            and isinstance(target.value, cst.Name)
            and target.value.value in {"self", "cls"}
        ):
            for receiver in self.names(target.value):
                cls = self.receivers.get(receiver)
                if cls:
                    self.attributes.setdefault(cls + "." + target.attr.value, set()).update(types)

    def visit_AnnAssign(self, node):
        if self.kinds[-1] == "class" and isinstance(node.target, cst.Name):
            self.attributes.setdefault(self.stack[-1] + "." + node.target.value, set()).update(
                self.names(node.annotation.annotation)
            )
        self.assignment(node.target, self.names(node.annotation.annotation))

    def visit_Assign(self, node):
        if isinstance(node.value, cst.Call):
            types = self.names(node.value.func)
        else:
            types = sorted({typ for name in self.names(node.value) for typ in self.bindings.get(name, [])})
        for target in node.targets:
            self.assignment(target.target, types)


MAX_CST_DEPTH = 100
MAX_CST_NODES = 1_000_000


class ExtractionLimitError(Exception):
    def __init__(self, stage, message):
        self.stage = stage
        super().__init__(message)


def check_tree(tree):
    # Iterator frames bound memory by depth rather than tree width.
    stack = [iter((tree,))]
    count = 0
    while stack:
        node = next(stack[-1], None)
        if node is None:
            stack.pop()
            continue
        count += 1
        if len(stack) > MAX_CST_DEPTH or count > MAX_CST_NODES:
            raise ExtractionLimitError(
                "complexity", f"Syntax tree exceeds depth {MAX_CST_DEPTH} or node budget {MAX_CST_NODES}"
            )
        stack.append(iter(node.children))


def extract(graph, module, file, source):
    stage = "parse"
    try:
        tree = cst.parse_module(source)
        stage = "complexity"
        check_tree(tree)
        # Fresh parser trees have unique nodes and need no defensive deep clone.
        wrapper = MetadataWrapper(tree, unsafe_skip_copy=True)
        stage = "metadata"
        wrapper.resolve_many(Extractor.METADATA_DEPENDENCIES)
        visitor = Extractor(graph, module, file, wrapper.module)
        stage = "extraction"
        wrapper.visit(visitor)
        return visitor
    except RecursionError as exc:
        raise ExtractionLimitError(stage, f"Recursion limit exceeded during {stage}") from exc
