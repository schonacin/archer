"""Backend-independent extraction contract and legacy fingerprint compatibility."""

import hashlib
import io
import tokenize
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
