"""Adapt a single native extraction result to the shared facts contract."""

import builtins
import io
import json
import keyword
import tokenize
from bisect import bisect_left, bisect_right

from archer import _native
from archer.graph.model import Edge, Node, resolution
from archer.scan.facts import ExtractionLimitError, ParsedModuleFacts, fingerprint

_BUILTINS = dir(builtins)


def _deindent(code, prefix):
    """Match LibCST code_for_node without changing multiline string contents."""
    if not prefix:
        return code
    lines = (prefix + code).splitlines(keepends=True)
    protected = set()
    for token in tokenize.generate_tokens(io.StringIO("".join(lines)).readline):
        if token.type in {
            tokenize.STRING,
            getattr(tokenize, "FSTRING_MIDDLE", -1),
            getattr(tokenize, "TSTRING_MIDDLE", -1),
        }:
            protected.update(range(token.start[0] + 1, token.end[0] + 1))
    return "".join(
        line[len(prefix) :] if i not in protected and line.startswith(prefix) else line
        for i, line in enumerate(lines, 1)
    )


class SourceText:
    def __init__(self, source):
        self.raw = source.encode()
        lines = source.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line.encode()))

        def offset(point):
            line, column = point
            return (
                offsets[line - 1] + len(lines[line - 1][:column].encode())
                if line <= len(lines)
                else len(self.raw)
            )

        self.tokens = []
        self.indents = []
        self.statements = []
        statement = 0
        indentation = [""]
        for t in tokenize.generate_tokens(io.StringIO(source).readline):
            if t.type == tokenize.NEWLINE:
                statement += 1
            elif t.type == tokenize.INDENT:
                indentation.append(t.string)
            elif t.type == tokenize.DEDENT:
                indentation.pop()
            elif t.type not in {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER}:
                self.tokens.append((offset(t.start), offset(t.end), t.string, t.type))
                self.indents.append(indentation[-1])
                self.statements.append(statement)
        self.starts = [t[0] for t in self.tokens]
        self.ends = [t[1] for t in self.tokens]
        self.pairs = {}
        stack = []
        for i, (_, _, text, kind) in enumerate(self.tokens):
            if kind != tokenize.OP:
                continue
            if text in {"(", "[", "{"}:
                stack.append(i)
            elif text in {")", "]", "}"} and stack:
                self.pairs[stack.pop()] = i

    def reference(self, start, end, kind):
        if kind != "imports":
            left = bisect_left(self.starts, start) - 1
            right = bisect_right(self.ends, end)
            while left >= 0 and right < len(self.tokens):
                if self.tokens[left][2] != "(" or self.pairs.get(left) != right:
                    break
                # A call/class argument delimiter belongs to its parent, not the expression.
                if left and self.statements[left - 1] == self.statements[left]:
                    previous = self.tokens[left - 1]
                    if previous[2] in {")", "]", "}"} or (
                        previous[3] == tokenize.NAME and not keyword.iskeyword(previous[2])
                    ):
                        break
                start, end = self.tokens[left][0], self.tokens[right][1]
                left -= 1
                right += 1
        code = self.raw[start:end].decode()
        if "\n" in code:
            prefix = self.indents[bisect_left(self.starts, start)]
            code = _deindent(code, prefix)
        return code


def extract(source, module, file):
    try:
        data = json.loads(_native.extract(source, module, file, _BUILTINS))
    except ValueError as exc:
        stage, _, message = str(exc).partition(": ")
        raise ExtractionLimitError(
            stage if stage in {"parse", "complexity"} else "extraction", message or str(exc)
        ) from exc
    text_source = SourceText(source)
    raw = text_source.raw
    facts = ParsedModuleFacts()
    for decl in data["nodes"]:
        start, end = decl["code_start"], decl["code_end"]
        prefix = raw[raw.rfind(b"\n", 0, start) + 1 : start].decode()
        code = _deindent(raw[start:end].decode(), prefix)
        ident = decl["id"]
        facts.nodes[ident] = Node(
            ident, decl["kind"], decl["name"], module, file, decl["range"], {"fingerprint": fingerprint(code)}
        )
        facts.edges.append(Edge(decl["parent"], ident, "contains", decl["range"], resolution()))
    for ref in data["pending"]:
        start, end = ref.pop("start"), ref.pop("end")
        text = text_source.reference(start, end, ref["kind"])
        ref["text"] = text
        ref["identity"] = fingerprint(text)
        facts.pending.append(ref)
    for name in ("exports", "attributes", "bindings"):
        setattr(facts, name, {k: set(v) for k, v in data[name].items()})
    facts.rebindings = set(data["rebindings"])
    return facts
