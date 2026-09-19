from collections import Counter
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
    for key in (
        "sites",
        "change",
        "before",
        "expression",
        "structural_initializer",
        "direct_fingerprint",
        "direct_change",
        "descendant_changes",
    ):
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
        direct_change = node.metadata["change"]
        if direct_change == "modified":
            left_direct = a.metadata.get("direct_fingerprint")
            right_direct = b.metadata.get("direct_fingerprint")
            if left_direct is not None and right_direct is not None:
                left_fields, right_fields = semantic(a), semantic(b)
                left_fields["metadata"].pop("fingerprint", None)
                right_fields["metadata"].pop("fingerprint", None)
                direct_change = (
                    "unchanged" if left_direct == right_direct and left_fields == right_fields else "modified"
                )
            else:
                direct_change = "unknown"
        node.metadata["direct_change"] = direct_change
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
    # Summaries are computed before projection so hidden methods still count in
    # types/modules views. Count direct symbol changes, not every changed owner.
    parents = {}
    for edge in graph.edges:
        if edge.kind == "contains":
            parents.setdefault(edge.target, set()).add(edge.source)
    children = {}
    for child, owners in parents.items():
        for owner in owners:
            children.setdefault(owner, set()).add(child)
    for ident, node in graph.nodes.items():
        # If no changed child explains a token-level difference (e.g. reordered
        # declarations), retain a direct change rather than silently losing it.
        if (
            node.metadata["change"] == "modified"
            and node.metadata["direct_change"] == "unchanged"
            and not any(
                graph.nodes[child].metadata["change"] != "unchanged" for child in children.get(ident, ())
            )
        ):
            node.metadata["direct_change"] = "modified"
    summaries = {}
    for ident, node in graph.nodes.items():
        status = node.metadata["direct_change"]
        if status == "unknown":
            status = "modified"
        if status not in {"added", "removed", "modified"}:
            continue
        pending, seen = list(parents.get(ident, ())), {ident}
        while pending:
            parent = pending.pop()
            if parent in seen:
                continue
            seen.add(parent)
            summaries.setdefault(parent, Counter())[(node.kind, status)] += 1
            pending.extend(parents.get(parent, ()))
    for ident, counts in summaries.items():
        graph.nodes[ident].metadata["descendant_changes"] = [
            {"kind": kind, "change": status, "count": count}
            for (kind, status), count in sorted(counts.items())
        ]
    return graph
