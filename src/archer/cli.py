"""Command-line boundary; scanning and projections also have Python APIs."""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from importlib.resources import files
from pathlib import Path

from archer import __version__
from archer.checks import check
from archer.config import boolean, parse_args
from archer.context import context
from archer.git import pair, snapshot
from archer.graph.algorithms import LEVELS, changes, neighborhood
from archer.graph.diff import diff
from archer.graph.model import Graph
from archer.render import d2_source, render
from archer.scan import scan
from archer.scan.cache import cache_info, clear_cache


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def parser():
    root = argparse.ArgumentParser(description="Static Python architecture graphs and Git changes")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("scan", "render", "context", "diff", "check"):
        cmd = commands.add_parser(name)
        cmd.add_argument(
            "--root",
            type=Path,
            default=Path(os.environ.get("ARCHER_ROOT", Path.cwd())),
            help="Python repository (default: ARCHER_ROOT or current directory)",
        )
        cmd.add_argument(
            "--source-root",
            action="append",
            help="Import root relative to repository; repeatable; auto-detects src",
        )
        cmd.add_argument("--parser", choices=("rust", "libcst"), help="Extraction backend (default: rust)")
        cmd.add_argument("--no-cache", action="store_true", help="Disable extraction cache reads and writes")
        cmd.add_argument("--cache-dir", type=Path, help="Override the user extraction cache directory")
        cmd.add_argument(
            "--cache-max-mb", type=nonnegative, help="Cache size limit in MiB (default: 512; 0 disables)"
        )
        cmd.add_argument("--exclude", action="append", help="Repository-relative glob; repeatable")
        cmd.add_argument(
            "-o",
            "--output",
            type=Path,
            help="Output path; - writes to stdout. Artifacts default to ./archer/.",
        )
        if name != "diff":
            cmd.add_argument("--graph", type=Path, help="Read canonical IR instead of scanning")
        if name in {"render", "context", "diff", "check"}:
            modes = cmd.add_mutually_exclusive_group()
            for mode in ("staged", "worktree", "unstaged"):
                modes.add_argument("--" + mode, dest="mode", action="store_const", const=mode)
        if name in {"render", "context"}:
            cmd.add_argument("--level", choices=sorted(LEVELS), default="modules")
            cmd.add_argument("--changes", action="store_true")
            cmd.add_argument("--radius", type=nonnegative, default=2)
            cmd.add_argument("--direction", choices=["both", "incoming", "outgoing"], default="both")
            cmd.add_argument("--focus", help="Exact module, symbol ID or qualified name")
        if name == "render":
            cmd.add_argument(
                "--exclude-arrows",
                action="append",
                default=[],
                metavar="MODULE",
                help="Hide arrows to and from a module and its children; repeatable",
            )
            cmd.add_argument(
                "--exclude-arrows-to",
                action="append",
                default=[],
                metavar="MODULE",
                help="Hide arrows into a module and its children; repeatable",
            )
            cmd.add_argument(
                "--exclude-arrows-from",
                action="append",
                default=[],
                metavar="MODULE",
                help="Hide arrows out of a module and its children; repeatable",
            )
            cmd.add_argument(
                "--color-arrows",
                type=boolean,
                nargs="?",
                const=True,
                default=True,
                metavar="{true,false}",
                help="Color arrows by source subsystem (default: true); ignored for Git diffs",
            )
            svg = cmd.add_mutually_exclusive_group()
            svg.add_argument(
                "--svg-optimization",
                choices=["raw", "medium", "fast"],
                default="fast",
                help="SVG post-processing: raw, medium, or approximate maskless fast (default)",
            )
            svg.add_argument(
                "--no-optimize-svg",
                dest="svg_optimization",
                action="store_const",
                const="raw",
                help="Alias for --svg-optimization raw",
            )
            cmd.add_argument("--format", choices=["d2", "svg", "png", "pdf"], default="svg")
            cmd.add_argument("--layout", choices=["auto", "tala", "elk", "dagre"], default="auto")
            cmd.add_argument(
                "--external", action="store_true", help="Include unresolved/external nodes in projected views"
            )
        if name == "context":
            cmd.add_argument("--max-chars", type=int, default=12000)
        if name == "diff":
            cmd.add_argument("revisions", nargs="*")
        if name == "check":
            cmd.add_argument("--baseline", help="Git revision to compare against current worktree")
            cmd.add_argument("--fan-threshold", type=nonnegative, default=15)
            cmd.add_argument("--coupling-threshold", type=nonnegative, default=0)
            cmd.add_argument("--format", choices=["text", "json"], default="text")
    cache = commands.add_parser("cache", help="Inspect or clear the extraction cache")
    actions = cache.add_subparsers(dest="cache_action", required=True)
    for action in ("info", "clear"):
        cmd = actions.add_parser(action)
        cmd.add_argument("--cache-dir", type=Path, help="Override the user extraction cache directory")
        cmd.add_argument("--format", choices=("text", "json"), default="text")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--format", choices=["text", "json"], default="text")
    commands.add_parser("skill", help="Print the bundled Agent Skill")
    return root


def configuration(args):
    root = args.root.resolve()
    if not root.is_dir():
        raise ValueError(f"Repository directory does not exist: {root}")
    path = root / "pyproject.toml"
    config = tomllib.loads(path.read_text()).get("tool", {}).get("archer", {}) if path.exists() else {}
    roots = args.source_root if args.source_root is not None else config.get("source_roots")
    excludes = args.exclude if args.exclude is not None else config.get("exclude", [])
    if roots is not None and (
        not isinstance(roots, list)
        or not all(
            isinstance(r, str) and r and not Path(r).is_absolute() and ".." not in Path(r).parts
            for r in roots
        )
    ):
        raise ValueError("source_roots must be a list of repository-relative paths")
    if not isinstance(excludes, list) or not all(isinstance(p, str) for p in excludes):
        raise ValueError("exclude must be a list of glob strings")
    backend = args.parser if args.parser is not None else config.get("parser", "rust")
    if not isinstance(backend, str) or backend not in {"rust", "libcst"}:
        raise ValueError("parser must be rust or libcst")
    cache = config.get("cache", True)
    cache_max_mb = args.cache_max_mb if args.cache_max_mb is not None else config.get("cache_max_mb", 512)
    configured_dir = config.get("cache_dir")
    if not isinstance(cache, bool):
        raise ValueError("cache must be a boolean")  # noqa: TRY004 -- report invalid CLI configuration
    if type(cache_max_mb) is not int or cache_max_mb < 0:
        raise ValueError("cache_max_mb must be a nonnegative integer")
    if configured_dir is not None and (not isinstance(configured_dir, str) or not configured_dir):
        raise ValueError("cache_dir must be a nonempty path string")
    directory = args.cache_dir
    if directory is None and configured_dir is not None:
        directory = Path(configured_dir).expanduser()
        if not directory.is_absolute():
            directory = root / directory
    return {
        "source_roots": roots,
        "excludes": excludes,
        "parser": backend,
        "cache": cache and not args.no_cache,
        "cache_dir": directory,
        "cache_max_mb": cache_max_mb,
    }


def emit(text, output=None):
    if output and output != Path("-"):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def filename_part(value):
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip(".-")[:90] or "snapshot"


def default_output(args, graph):
    """Artifact names describe the projection; diagnostics still go to stdout."""
    if args.command not in {"scan", "diff", "render", "context"}:
        return None
    is_diff = graph.metadata.get("diff", False)
    if args.command in {"scan", "diff"}:
        parts, extension = ["graph"], "json"
    else:
        parts = ["architecture" if args.command == "render" else "context"]
        extension = args.format if args.command == "render" else "md"
    if is_diff:
        parts.append("diff" if args.command in {"scan", "diff"} else "changes")
        before, after = graph.metadata.get("before") or {}, graph.metadata.get("after") or {}
        kinds = before.get("kind"), after.get("kind")
        mode = {
            ("COMMIT", "INDEX"): "staged",
            ("INDEX", "WORKTREE"): "unstaged",
            ("COMMIT", "WORKTREE"): "worktree",
        }.get(kinds)
        if mode:
            parts.append(mode)
        elif before.get("revision") and after.get("revision"):
            parts.append(before["revision"][:8] + "-to-" + after["revision"][:8])
    if args.command in {"render", "context"}:
        parts.append("full" if args.level == "changes" else args.level)
        if args.focus:
            parts.extend(["focus", filename_part(args.focus)])
        if args.focus or args.changes or args.level == "changes":
            parts.append(f"r{args.radius}")
            if args.direction != "both":
                parts.append(args.direction)
    if args.command == "render":
        for direction, modules in (
            ("", args.exclude_arrows),
            ("-to", args.exclude_arrows_to),
            ("-from", args.exclude_arrows_from),
        ):
            for module in modules:
                parts.extend(["without-arrows" + direction, module])
        if args.external and args.level not in {"full", "changes"}:
            parts.append("external")
        if not args.color_arrows and not is_diff:
            parts.append("plain-arrows")
        if args.svg_optimization != "fast" and args.format == "svg":
            parts.append(args.svg_optimization)
    return Path("archer") / ("-".join(filename_part(p) for p in parts) + "." + extension)


def doctor():
    backends = {}
    for name in ("git", "d2", "d2plugin-tala", "ty", "pyright"):
        executable = shutil.which(name)
        sibling = Path(sys.executable).parent / name
        if not executable and sibling.is_file():
            executable = str(sibling)
        info = {"available": bool(executable), "path": executable}
        if executable and name in {"git", "d2"}:
            result = subprocess.run(
                [executable, "--version"], check=False, capture_output=True, text=True, timeout=10
            )
            info["version"] = (result.stdout or result.stderr).strip()
            if name == "d2":
                layouts = subprocess.run(
                    [executable, "layout"], check=False, capture_output=True, text=True, timeout=10
                )
                info["layouts"] = re.findall(r"(?m)^(\w+) \(", layouts.stdout + layouts.stderr)
        backends[name] = info
    return {
        "archer": __version__,
        "backends": backends,
        "semantic_enrichment": "reserved for future providers; v1 uses Archer static resolution",
    }


def run(args):
    if args.command == "cache":
        try:
            if args.cache_action == "clear":
                clear_cache(args.cache_dir)
            report = cache_info(args.cache_dir)
        except (sqlite3.Error, OSError) as exc:
            raise ValueError(f"Cache operation failed: {exc}") from exc
        if args.format == "json":
            emit(json.dumps(report, indent=2) + "\n")
        else:
            prefix = "Cleared cache" if args.cache_action == "clear" else "Cache"
            emit(
                f"{prefix}: {report['directory']}\n{report['entries']} entries; {report['bytes'] / 1024**2:.2f} MiB on disk\n"
            )
            if report.get("error"):
                emit(f"Cache unavailable: {report['error']}\n")
        return 2 if report.get("error") else 0
    if args.command == "doctor":
        report = doctor()
        emit(
            json.dumps(report, indent=2) + "\n"
            if args.format == "json"
            else "\n".join(
                [f"Archer {__version__}"]
                + [
                    f"{name}: {info['path'] or 'unavailable'}"
                    + ("; layouts: " + ", ".join(info["layouts"]) if info.get("layouts") else "")
                    for name, info in report["backends"].items()
                ]
                + [report["semantic_enrichment"]]
            )
            + "\n"
        )
        return 0
    if args.command == "skill":
        emit(files("archer").joinpath("skills/archer/SKILL.md").read_text())
        return 0
    kwargs = configuration(args)
    mode = getattr(args, "mode", None)
    baseline = None
    if args.command == "diff":
        before, after = pair(args.root, args.revisions, mode, **kwargs)
        graph = diff(before, after)
    elif args.command == "check" and (mode or args.baseline):
        if mode and args.baseline:
            raise ValueError("Use --baseline or a snapshot mode, not both")
        if args.graph:
            raise ValueError("--graph cannot be combined with Git snapshot options")
        if args.baseline:
            baseline = snapshot(args.root, args.baseline, **kwargs)
            graph = snapshot(args.root, "WORKTREE", **kwargs)
        else:
            baseline, graph = pair(args.root, mode=mode, **kwargs)
    elif args.graph:
        if mode:
            raise ValueError("--graph cannot be combined with Git snapshot options")
        try:
            graph = Graph.from_dict(json.loads(args.graph.read_text()))
        except RecursionError as exc:
            raise ValueError("Graph JSON exceeds supported nesting depth") from exc
    elif getattr(args, "changes", False) or getattr(args, "level", None) == "changes" or mode:
        before, after = pair(args.root, mode=mode, **kwargs)
        graph = diff(before, after)
    else:
        graph = scan(args.root, **kwargs)
    if getattr(args, "changes", False) or getattr(args, "level", None) == "changes":
        graph = changes(graph, args.radius, args.direction)
    if getattr(args, "focus", None):
        found = [n for n in graph.nodes.values() if n.id == args.focus or n.qualified_name == args.focus]
        if not found:
            raise ValueError(f"Unknown focus: {args.focus}")
        graph = neighborhood(graph, {n.module for n in found}, args.radius, args.direction)
    if args.command == "render":
        requested_modules = {
            *args.exclude_arrows,
            *args.exclude_arrows_to,
            *args.exclude_arrows_from,
        }
        known_modules = set(graph.metadata.get("modules", [])) | {
            node.module for node in graph.nodes.values() if node.module
        }
        unknown = sorted(requested_modules - known_modules)
        if unknown:
            raise ValueError("Unknown arrow-exclusion module(s): " + ", ".join(unknown))
    if args.output is None:
        args.output = default_output(args, graph)
    if args.command in {"scan", "diff"}:
        emit(graph.to_json(), args.output)
    elif args.command == "render":
        if args.format == "d2":
            emit(
                d2_source(
                    graph,
                    args.level,
                    args.external,
                    args.color_arrows,
                    args.exclude_arrows,
                    args.exclude_arrows_to,
                    args.exclude_arrows_from,
                ),
                args.output,
            )
        else:
            output = args.output
            if output != Path("-") and output.suffix.lower() != "." + args.format:
                raise ValueError("Output extension must match --format")
            with tempfile.TemporaryDirectory(prefix="archer-stdout-") as directory:
                target = Path(directory) / ("output." + args.format) if output == Path("-") else output
                report = render(
                    graph,
                    target,
                    level=args.level,
                    external=args.external,
                    layout=args.layout,
                    color_arrows=args.color_arrows,
                    svg_optimization=args.svg_optimization,
                    exclude_arrows=args.exclude_arrows,
                    exclude_arrows_to=args.exclude_arrows_to,
                    exclude_arrows_from=args.exclude_arrows_from,
                )
                if output == Path("-"):
                    sys.stdout.buffer.write(target.read_bytes())
                    report["output"] = "stdout"
            print(json.dumps(report), file=sys.stderr)
    elif args.command == "context":
        emit(context(graph, args.max_chars, args.level), args.output)
    elif args.command == "check":
        if graph.metadata.get("diff"):
            raise ValueError("Checks need a current graph, not a diff graph; use a snapshot mode")
        report = check(graph, baseline, args.fan_threshold, args.coupling_threshold)
        if args.format == "json":
            emit(json.dumps(report, indent=2) + "\n", args.output)
        else:
            emit(
                "\n".join(f"{f['severity']}: {f['rule']}: {f['message']}" for f in report["findings"])
                + ("\n" if report["findings"] else "No architecture findings.\n"),
                args.output,
            )
        if report["diagnostics"]:
            print(
                f"Incomplete scan: {len(report['diagnostics'])} diagnostic(s); inspect --format json.",
                file=sys.stderr,
            )
            return 2
        return 1 if report["findings"] else 0
    if args.output and args.output != Path("-") and (args.command != "render" or args.format == "d2"):
        print(f"Wrote {args.output}", file=sys.stderr)
    if graph.metadata.get("diagnostics"):
        print(
            f"Incomplete scan: {len(graph.metadata['diagnostics'])} diagnostic(s); inspect IR metadata.",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv=None):
    try:
        return run(parse_args(parser(), sys.argv[1:] if argv is None else argv))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"archer: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
