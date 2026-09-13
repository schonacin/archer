"""Canonical IR. All projections consume this graph, including semantic diffs."""

import json
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "1.0"


@dataclass
class Node:
    id: str
    kind: str
    qualified_name: str
    module: str
    file: str | None
    source_range: dict | None
    metadata: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    kind: str
    source_range: dict | None
    resolution: dict
    metadata: dict = field(default_factory=dict)

    @property
    def key(self):
        # Architecture relationships aggregate call sites, not syntax positions.
        return self.source, self.target, self.kind


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def add_edge(self, edge):
        self.edges.append(edge)

    def canonicalize(self):
        merged = {}
        for edge in self.edges:
            if edge.key not in merged:
                merged[edge.key] = edge
                edge.metadata.setdefault("sites", [edge.source_range] if edge.source_range else [])
            else:
                previous = merged[edge.key]
                for site in edge.metadata.get("sites", [edge.source_range] if edge.source_range else []):
                    if site not in previous.metadata["sites"]:
                        previous.metadata["sites"].append(site)
                if edge.resolution["confidence"] < previous.resolution["confidence"]:
                    previous.resolution = edge.resolution
        self.edges = [merged[k] for k in sorted(merged)]
        for edge in self.edges:
            edge.metadata["sites"].sort(key=lambda s: (s["start"]["line"], s["start"]["column"]))
        return self

    def to_dict(self):
        self.validate()
        return {
            "schema_version": self.schema_version,
            "metadata": self.metadata,
            "nodes": [asdict(self.nodes[k]) for k in sorted(self.nodes)],
            "edges": [asdict(e) for e in sorted(self.edges, key=lambda e: e.key)],
        }

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data):
        try:
            if data.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"Unsupported graph schema: {data.get('schema_version')}")
            nodes = [Node(**n) for n in data["nodes"]]
            if len({n.id for n in nodes}) != len(nodes):
                raise ValueError("Duplicate node IDs")
            graph = cls(
                {n.id: n for n in nodes}, [Edge(**e) for e in data["edges"]], data.get("metadata", {})
            )
            graph.validate()
            return graph
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"Malformed Archer graph: {exc}") from exc

    def validate(self):
        if not isinstance(self.metadata, dict):
            raise TypeError("Graph metadata must be an object")
        for ident, node in self.nodes.items():
            if not isinstance(ident, str) or not ident or node.id != ident:
                raise ValueError("Invalid node ID")
            if node.kind not in {
                "package",
                "module",
                "class",
                "method",
                "function",
                "external",
                "unresolved",
            }:
                raise ValueError(f"Invalid node kind: {node.kind}")
            if (
                not isinstance(node.metadata, dict)
                or not isinstance(node.qualified_name, str)
                or not isinstance(node.module, str)
            ):
                raise TypeError("Invalid node fields")
            validate_range(node.source_range)
        for edge in self.edges:
            if edge.source not in self.nodes or edge.target not in self.nodes:
                raise ValueError(f"Dangling edge: {edge.key}")
            if edge.kind not in {"contains", "imports", "calls", "inherits"}:
                raise ValueError(f"Invalid edge kind: {edge.kind}")
            if not isinstance(edge.metadata, dict):
                raise TypeError("Edge metadata must be an object")
            if edge.resolution["status"] not in {"exact", "strong", "inferred", "ambiguous", "unresolved"}:
                raise ValueError("Invalid resolution status")
            confidence = edge.resolution["confidence"]
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ValueError("Invalid resolution confidence")
            if not isinstance(edge.resolution["provider"], str) or not isinstance(
                edge.resolution["evidence"], list
            ):
                raise TypeError("Invalid resolution provenance")
            validate_range(edge.source_range)


def validate_range(value):
    if value is None:
        return
    for point in (value["start"], value["end"]):
        if (
            not isinstance(point["line"], int)
            or point["line"] < 1
            or not isinstance(point["column"], int)
            or point["column"] < 0
        ):
            raise ValueError("Invalid source position")
    if (value["end"]["line"], value["end"]["column"]) < (value["start"]["line"], value["start"]["column"]):
        raise ValueError("Source range ends before it starts")


def resolution(status="exact", evidence="declaration", provider="archer"):
    return {
        "status": status,
        "confidence": {"exact": 1.0, "strong": 0.9, "inferred": 0.65, "ambiguous": 0.25, "unresolved": 0.0}[
            status
        ],
        "provider": provider,
        "evidence": [evidence],
    }
