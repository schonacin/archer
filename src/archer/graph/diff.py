from copy import deepcopy
from dataclasses import asdict

from archer.graph.model import Graph


def semantic(value):
    data = asdict(value)
    data.pop("source_range", None)
    # Schema 1.0 graphs named LibCST in this evidence even though Archer owns
    # resolution. Treat the backend-neutral wording as the same evidence.
    if "resolution" in data:
        data["resolution"]["evidence"] = [
            "lexical binding and repository declaration"
            if item == "LibCST lexical binding and repository declaration"
            else item
            for item in data["resolution"].get("evidence", [])
        ]
    if data.get("kind") == "unresolved":
        data.pop("qualified_name", None)
    metadata = data.get("metadata", {})
    for key in ("sites", "change", "before", "expression"):
        metadata.pop(key, None)
    return data


def diff(before, after):
    graph = Graph(
        metadata={
            "diff": True,
            "before": before.metadata.get("snapshot"),
            "after": after.metadata.get("snapshot"),
            "diagnostics": before.metadata.get("diagnostics", []) + after.metadata.get("diagnostics", []),
        }
    )
    for key in sorted(before.nodes.keys() | after.nodes.keys()):
        a, b = before.nodes.get(key), after.nodes.get(key)
        node = deepcopy(b or a)
        node.metadata["change"] = (
            "added"
            if a is None
            else "removed"
            if b is None
            else "unchanged"
            if semantic(a) == semantic(b)
            else "modified"
        )
        if a and node.metadata["change"] == "modified":
            node.metadata["before"] = asdict(a)
        graph.nodes[key] = node
    left, right = {e.key: e for e in before.edges}, {e.key: e for e in after.edges}
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        edge = deepcopy(b or a)
        edge.metadata["change"] = (
            "added"
            if a is None
            else "removed"
            if b is None
            else "unchanged"
            if semantic(a) == semantic(b)
            else "modified"
        )
        if a and edge.metadata["change"] == "modified":
            edge.metadata["before"] = asdict(a)
        graph.edges.append(edge)
    return graph
