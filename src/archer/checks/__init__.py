"""Small extensible check registry. Rules receive current and optional baseline metrics."""

from archer.graph.algorithms import metrics


def cycles(current, baseline, config):
    for component in current["components"]:
        yield {
            "rule": "dependency-cycle",
            "severity": "warning",
            "modules": component,
            "message": "Strongly connected dependency component: " + ", ".join(component),
        }
        if baseline is not None:
            old = [set(c) for c in baseline["components"]]
            if set(component) not in old:
                growing = any(c < set(component) for c in old)
                yield {
                    "rule": "growing-scc" if growing else "new-scc",
                    "severity": "warning",
                    "modules": component,
                    "message": "Dependency component grew" if growing else "New dependency component",
                }


def fans(current, baseline, config):
    for metric in ("fan_in", "fan_out"):
        for module, count in current[metric].items():
            if count > config["fan_threshold"]:
                yield {
                    "rule": "high-" + metric.replace("_", "-"),
                    "severity": "warning",
                    "modules": [module],
                    "message": f"{module}: {metric} {count} exceeds {config['fan_threshold']}",
                }


def coupling(current, baseline, config):
    if baseline is not None:
        delta = current["dependencies"] - baseline["dependencies"]
        if delta > config["coupling_threshold"]:
            yield {
                "rule": "coupling-increase",
                "severity": "warning",
                "modules": [],
                "message": f"Module dependencies increased by {delta} ({baseline['dependencies']} -> {current['dependencies']})",
            }


RULES = [cycles, fans, coupling]


def check(graph, baseline=None, fan_threshold=15, coupling_threshold=0):
    current = metrics(graph)
    previous = metrics(baseline) if baseline is not None else None
    config = {"fan_threshold": fan_threshold, "coupling_threshold": coupling_threshold}
    findings = [finding for rule in RULES for finding in rule(current, previous, config)]
    return {
        "metrics": current,
        "baseline_metrics": previous,
        "findings": findings,
        "diagnostics": graph.metadata.get("diagnostics", [])
        + (baseline.metadata.get("diagnostics", []) if baseline else []),
    }
