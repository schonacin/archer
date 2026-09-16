"""Compare parser phases without adding nondeterministic timing data to the IR."""

import argparse
import importlib
import json
from statistics import median
from time import perf_counter
from unittest.mock import patch

from archer.graph.model import Graph
from archer.scan.parser import get_parser
from archer.scan.resolver import Resolver

scanner = importlib.import_module("archer.scan")


def measure(sources, backend):
    phases = dict.fromkeys(("extraction", "module_fingerprints", "resolution", "finalization"), 0.0)

    def timed(name, function):
        def call(*args, **kwargs):
            start = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                phases[name] += perf_counter() - start

        return call

    extract = timed("extraction", get_parser(backend))
    with (
        patch.object(scanner, "get_parser", return_value=extract),
        patch.object(scanner, "fingerprint", timed("module_fingerprints", scanner.fingerprint)),
        patch.object(Resolver, "run", timed("resolution", Resolver.run)),
        patch.object(Graph, "canonicalize", timed("finalization", Graph.canonicalize)),
    ):
        start = perf_counter()
        graph = scanner.scan_sources(sources, parser=backend, cache=False)
        elapsed = perf_counter() - start
    phases["resolution"] -= phases["finalization"]
    phases["other"] = elapsed - sum(phases.values())
    phases["total"] = elapsed
    return graph, phases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    sources = scanner.read_sources(args.root)
    report = {"files": len(sources), "seconds": {}, "diagnostics": {}}
    graphs = {}
    for backend in ("rust", "libcst"):
        measurements = []
        for _ in range(args.repeat):
            graph, phases = measure(sources, backend)
            measurements.append(phases)
        graphs[backend] = graph.to_dict()
        report["seconds"][backend] = {
            phase: round(median(m[phase] for m in measurements), 6) for phase in measurements[0]
        }
        report["diagnostics"][backend] = len(graph.metadata["diagnostics"])
    report["identical_ir"] = graphs["rust"] == graphs["libcst"]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
