import argparse
import json
from pathlib import Path

import pytest
import yaml

from archer.cli import main, parser
from archer.config import command_parsers, options, parse_args
from archer.scan import scan


def test_every_command_flag_can_be_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "archer.yaml"
    for name, command in command_parsers(parser()).items():
        children = command_parsers(command)
        targets = (
            [(name.split(), command)]
            if not children
            else [([name, child], subparser) for child, subparser in children.items()]
        )
        for invocation, target in targets:
            for key, action in options(target).items():
                if isinstance(action, (argparse._StoreTrueAction, argparse._StoreConstAction)):
                    value, arguments = True, ["--" + key]
                else:
                    value = (
                        action.choices[-1]
                        if action.choices
                        else {
                            "root": str(tmp_path),
                            "cache-dir": str(tmp_path / "cache"),
                            "output": "-",
                            "graph": str(tmp_path / "graph.json"),
                            "radius": 3,
                            "max-chars": 100,
                            "fan-threshold": 5,
                            "coupling-threshold": 6,
                            "cache-max-mb": 0,
                            "color-arrows": False,
                        }.get(key, "sample")
                    )
                    arguments = ["--" + key, str(value).lower() if isinstance(value, bool) else str(value)]
                    if isinstance(action, argparse._AppendAction):
                        value = [value]
                section = {key: value}
                if children:
                    section = {invocation[-1]: section}
                config.write_text(yaml.safe_dump({name: section}))
                actual = parse_args(parser(), invocation)
                expected = parser().parse_args([*invocation, *arguments])
                assert vars(actual) == vars(expected), (invocation, key)


def test_yaml_precedence_paths_and_cli_lists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.archer]\nexclude=["toml.py"]\nparser="libcst"')
    (tmp_path / "archer.yaml").write_text(
        "exclude: [shared.py]\nparser: rust\nrender:\n  exclude: [command.py]\n"
        "  output: diagram.d2\n  format: d2\n  color-arrows: false\n"
    )
    args = parse_args(parser(), ["render"])
    assert args.exclude == ["command.py"]
    assert args.output == tmp_path / "diagram.d2"
    assert args.color_arrows is False
    args = parse_args(parser(), ["render", "--exclude", "cli.py", "--color-arrows=true", "-o", "-"])
    assert args.exclude == ["cli.py"] and args.output == Path("-") and args.color_arrows
    for name in ("toml", "shared", "command"):
        (tmp_path / f"{name}.py").write_text("x=1")
    assert main(["scan", "-o", "result.json"]) == 0
    assert json.loads((tmp_path / "result.json").read_text())["metadata"]["modules"] == ["command", "toml"]


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "render: []",
        "radius: [",
        "unknown: true",
        "render: {layout: unknown}",
        "render: {color-arrows: perhaps}",
        "scan: {no-cache: 'false'}",
        "scan: {exclude: foo.py}",
        "render: {radius: -1}",
        "render: {radius: 1.5}",
        "scan: {cache-max-mb: true}",
        "render: {staged: true, worktree: true}",
        "render: {svg-optimization: fast, no-optimize-svg: true}",
    ],
)
def test_invalid_yaml_is_clean_error(tmp_path, monkeypatch, capsys, content):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "archer.yaml").write_text(content)
    command = "scan" if content.startswith("scan:") else "render"
    assert main([command]) == 2
    assert "archer.yaml" in capsys.readouterr().err


def test_cli_overrides_configured_mutually_exclusive_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "archer.yaml").write_text("render: {staged: true, no-optimize-svg: true}")
    args = parse_args(parser(), ["render", "--worktree", "--svg-optimization", "medium"])
    assert args.mode == "worktree" and args.svg_optimization == "medium"


def test_optional_config_discovery_and_configured_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert parse_args(parser(), ["render"]).svg_optimization == "fast"
    assert parse_args(parser(), ["render"]).color_arrows is True
    (tmp_path / "archer.yaml").write_text("")
    assert parse_args(parser(), ["render"]).color_arrows is True
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "archer.yaml").write_text("render: {color-arrows: false}")
    assert parse_args(parser(), ["render", "--root", str(repo)]).color_arrows is False
    monkeypatch.setenv("ARCHER_ROOT", str(repo))
    assert parse_args(parser(), ["render"]).color_arrows is False
    monkeypatch.delenv("ARCHER_ROOT")
    (tmp_path / "archer.yaml").write_text("root: repo")
    assert parse_args(parser(), ["scan"]).root == repo


def test_archerignore_patterns_and_excludes(tmp_path):
    for name in ("ok.py", "bad.py", "nested/bad.py", "generated/a.py", "generated/keep.py", "skip/a.py"):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("def broken(:" if "bad" in name else "x=1")
    (tmp_path / ".archerignore").write_text(
        "# ignored sources\n\nbad.py\ngenerated/*\n!generated/keep.py\n/skip/\n"
    )
    graph = scan(tmp_path)
    assert graph.metadata["modules"] == ["generated", "generated.keep", "ok"]
    assert graph.metadata["diagnostics"] == []
    assert scan(tmp_path, excludes=["generated/*"]).metadata["modules"] == ["ok"]


def test_command_section_overrides_shared_exclusive_group(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "archer.yaml").write_text(
        "staged: true\nno-optimize-svg: true\nrender: {worktree: true, svg-optimization: medium}"
    )
    args = parse_args(parser(), ["render"])
    assert args.mode == "worktree" and args.svg_optimization == "medium"
