"""Backend-independent extraction contract and legacy fingerprint compatibility."""

import ast
import hashlib
import io
import tokenize
from collections import Counter
from dataclasses import dataclass, field

from archer.graph.model import Edge, Node


def fingerprint(code):
    """Ignore comments/formatting, but retain literals and executable tokens."""
    tokens = [
        (t.type, "" if t.type == tokenize.INDENT else t.string)
        for t in tokenize.generate_tokens(io.StringIO(code).readline)
        if t.type
        not in {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING, tokenize.ENDMARKER}
    ]
    return hashlib.sha256(repr(tokens).encode()).hexdigest()


def direct_fingerprints(source, module):
    """Fingerprint each scope without charging it for nested declarations.

    Definition headers/decorators belong to their declared symbol. Compound
    statements and assignments belong to the surrounding scope. Attribution
    is optional when the runtime AST cannot parse syntax accepted by a backend.
    """
    fingerprints, counts = {}, Counter()

    class Scopes(ast.NodeTransformer):
        scope = module

        def visit_definition(self, node):
            parent = self.scope
            name = parent + "." + node.name
            counts[name] += 1
            self.scope = name if counts[name] == 1 else f"{name}#{counts[name]}"
            self.generic_visit(node)
            fingerprints[self.scope] = digest(node)
            self.scope = parent
            # Returning None removes this declaration from its parent scope.

        visit_FunctionDef = visit_definition
        visit_AsyncFunctionDef = visit_definition
        visit_ClassDef = visit_definition

        def visit_Pass(self, node):
            # Empty scopes often acquire or lose a placeholder with a child.
            return None

    def digest(node):
        return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()

    try:
        tree = Scopes().visit(ast.parse(source))
        fingerprints[module] = digest(tree)
    except (SyntaxError, RecursionError, ValueError):
        return {}
    return fingerprints


class ExtractionLimitError(Exception):
    def __init__(self, stage, message):
        self.stage = stage
        super().__init__(message)


@dataclass
class ParsedModuleFacts:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    exports: dict[str, set[str]] = field(default_factory=dict)
    attributes: dict[str, set[str]] = field(default_factory=dict)
    bindings: dict[str, set[str]] = field(default_factory=dict)
    rebindings: set[str] = field(default_factory=set)
    direct_fingerprints: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_extractor(cls, graph, visitor):
        return cls(
            graph.nodes,
            graph.edges,
            visitor.pending,
            visitor.exports,
            visitor.attributes,
            visitor.bindings,
            visitor.rebindings,
        )
