"""Conservative, Archer-owned reference resolution; never drops a call."""

import hashlib

from archer.graph.model import Edge, Node, resolution

MAX_RESOLUTION_STEPS = 10_000
MAX_ALIAS_LENGTH = 4096


class ResolutionLimitError(Exception):
    pass


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
        self.steps = 0

    def consume_step(self):
        self.steps += 1
        if self.steps > MAX_RESOLUTION_STEPS:
            raise ResolutionLimitError(f"Reference resolution exceeds {MAX_RESOLUTION_STEPS} steps")

    def aliases(self, name):
        result, active = set(), set()
        stack = [(name, False)]
        while stack:
            name, leaving = stack.pop()
            if leaving:
                active.remove(name)
                continue
            self.consume_step()
            if len(name) > MAX_ALIAS_LENGTH:
                raise ResolutionLimitError(f"Alias expansion exceeds {MAX_ALIAS_LENGTH} characters")
            if name in active:
                result.add(name)
                continue
            active.add(name)
            stack.append((name, True))
            parts = name.split(".")
            for i in range(len(parts), 0, -1):
                self.consume_step()
                prefix = ".".join(parts[:i])
                targets = self.exports.get(prefix, set()) - {prefix}
                if targets:
                    for target in sorted(targets, reverse=True):
                        stack.append((".".join([target] + parts[i:]), False))
                    break
            else:
                result.add(name)
        return result

    def candidates(self, names):
        return sorted({target for name in sorted(names) for target in self.aliases(name)})

    def member(self, cls, suffix):
        result, seen = set(), set()
        stack = [cls]
        while stack:
            cls = stack.pop()
            self.consume_step()
            if cls in seen:
                continue
            seen.add(cls)
            direct = self.by_name.get(cls + "." + suffix, [])
            if direct:
                result.update(direct)
            else:
                stack.extend(self.bases.get(cls, []))
        return sorted(result)

    def targets(self, ref):
        candidates = self.candidates(ref["candidates"])
        found = sorted({target for name in candidates for target in self.by_name.get(name, [])})
        if found:
            if len(candidates) > 1 or len(found) > 1 or any(n in self.rebindings for n in candidates):
                return found, "ambiguous", "multiple lexical bindings or reassignment"
            return found, "exact", "lexical binding and repository declaration"
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
            self.steps = 0
            try:
                targets, status, evidence = self.targets(ref)
                candidates = self.candidates(ref["candidates"]) if not targets else []
            except (ResolutionLimitError, RecursionError) as exc:
                # Never turn partial search results into confident relationships.
                targets, candidates, status = [], [], "unresolved"
                evidence = str(exc) or "Recursion limit exceeded during resolution"
                self.graph.metadata["diagnostics"].append(
                    {
                        "file": self.graph.nodes[ref["source"]].file,
                        "severity": "error",
                        "stage": "resolution",
                        "range": ref["range"],
                        "message": evidence,
                    }
                )
            if not targets:
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
