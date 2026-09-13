"""Conservative, Archer-owned reference resolution; never drops a call."""

import hashlib

from archer.graph.model import Edge, Node, resolution


class Resolver:
    def __init__(self, graph, visitors):
        self.graph = graph
        self.exports = {}
        self.attributes = {}
        self.bindings = {}
        self.rebindings = set()
        self.pending = []
        self.by_name = {}
        for node in graph.nodes.values():
            self.by_name.setdefault(node.qualified_name, []).append(node.id)
        for visitor in visitors:
            self.exports.update(visitor.exports)
            self.attributes.update(visitor.attributes)
            self.bindings.update(visitor.bindings)
            self.rebindings.update(visitor.rebindings)
            self.pending.extend(visitor.pending)
        self.bases = {}

    def aliases(self, name, seen=None):
        seen = set() if seen is None else seen
        if name in seen:
            return {name}
        seen.add(name)
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            targets = self.exports.get(prefix, set()) - {prefix}
            if targets:
                return {
                    value
                    for target in targets
                    for value in self.aliases(".".join([target] + parts[i:]), seen.copy())
                }
        return {name}

    def candidates(self, names):
        return sorted({target for name in names for target in self.aliases(name)})

    def member(self, cls, suffix, seen=None):
        seen = set() if seen is None else seen
        if cls in seen:
            return []
        seen.add(cls)
        direct = self.by_name.get(cls + "." + suffix, [])
        if direct:
            return direct
        return sorted(
            {target for base in self.bases.get(cls, []) for target in self.member(base, suffix, seen.copy())}
        )

    def targets(self, ref):
        candidates = self.candidates(ref["candidates"])
        found = sorted({target for name in candidates for target in self.by_name.get(name, [])})
        if found:
            if len(candidates) > 1 or len(found) > 1 or any(n in self.rebindings for n in candidates):
                return found, "ambiguous", "multiple lexical bindings or reassignment"
            return found, "exact", "LibCST lexical binding and repository declaration"
        # Receiver identity comes from lexical metadata, not the spelling 'self'.
        for candidate in ref["candidates"]:
            parts = candidate.split(".")
            for i in range(len(parts) - 1, 0, -1):
                receiver, suffix = ".".join(parts[:i]), ".".join(parts[i:])
                cls = ref["receivers"].get(receiver)
                if cls and receiver not in self.rebindings:
                    found = self.member(cls, suffix)
                    if found:
                        return (
                            found,
                            "strong" if len(found) == 1 else "ambiguous",
                            "receiver class or inherited member; dynamic dispatch possible",
                        )
                    attr, dot, rest = suffix.partition(".")
                    if dot:
                        types = self.candidates(self.attributes.get(cls + "." + attr, []))
                        found = sorted({t for typ in types for t in self.member(typ, rest)})
                        if found:
                            return (
                                found,
                                "inferred" if len(found) == 1 and len(types) == 1 else "ambiguous",
                                "receiver attribute annotation or constructor assignment",
                            )
                types = self.candidates(self.bindings.get(receiver, []))
                found = sorted({t for typ in types for t in self.member(typ, suffix)})
                if found:
                    return (
                        found,
                        "inferred" if len(found) == 1 and len(types) == 1 else "ambiguous",
                        "annotated parameter or constructor binding",
                    )
        return [], "unresolved", ref["hint"] or "no unique repository declaration"

    def run(self):
        for ref in sorted(self.pending, key=lambda r: r["kind"] != "inherits"):
            targets, status, evidence = self.targets(ref)
            if not targets:
                candidates = self.candidates(ref["candidates"])
                text = ref["text"]
                external = bool(candidates) and all(
                    not any(n == m or n.startswith(m + ".") for m in self.graph.metadata["modules"])
                    for n in candidates
                )
                name = candidates[0] if len(candidates) == 1 else text
                if external and len(candidates) == 1:
                    ident, kind = "external:" + name, "external"
                    if ref["kind"] == "imports":
                        status, evidence = "strong", "explicit import; external declaration not inspected"
                else:
                    digest = hashlib.sha256((ref["source"] + "\0" + ref["identity"]).encode()).hexdigest()[
                        :16
                    ]
                    ident, kind = "unresolved:" + digest, "unresolved"
                if len(candidates) > 1:
                    status, evidence = "ambiguous", "multiple candidate bindings: " + ", ".join(candidates)
                self.graph.nodes.setdefault(
                    ident,
                    Node(ident, kind, name, "", None, None, {"expression": text, "candidates": candidates}),
                )
                targets = [ident]
            for target in targets:
                self.graph.add_edge(
                    Edge(
                        ref["source"],
                        target,
                        ref["kind"],
                        ref["range"],
                        resolution(status, evidence),
                        {"expression": ref["text"]},
                    )
                )
                if ref["kind"] == "inherits" and self.graph.nodes[target].kind == "class":
                    self.bases.setdefault(ref["source"], []).append(target)
        return self.graph.canonicalize()
