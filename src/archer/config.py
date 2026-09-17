"""Optional YAML command defaults, validated against the CLI's own arguments."""

import argparse
import os
from pathlib import Path

import yaml


def boolean(value):
    if isinstance(value, bool):
        return value
    if value in ("true", "false"):
        return value == "true"
    raise argparse.ArgumentTypeError("must be true or false")


def command_parsers(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def options(parser):
    return {
        option[2:]: action
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }


def mapping(value, label):
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a mapping of flag names to defaults")
    return {key.replace("_", "-"): item for key, item in value.items()}


def parse_args(parser, argv):
    """Discover config from explicit --root, ARCHER_ROOT, or cwd; CLI always wins."""
    initial = parser.parse_args(argv)
    root = getattr(initial, "root", Path(os.environ.get("ARCHER_ROOT", Path.cwd())))
    path = root.resolve() / "archer.yaml"
    if not path.is_file():
        return initial
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return initial
    data = mapping(data, str(path))
    commands = command_parsers(parser)
    selected = commands[initial.command]
    shared = {key: value for key, value in data.items() if key not in commands}
    known = {key for command in commands.values() for key in options(command)}
    unknown = shared.keys() - known
    if unknown:
        raise ValueError(f"{path}: unknown flag(s): {', '.join(sorted(unknown))}")
    defaults = {key: value for key, value in shared.items() if key in options(selected)}
    section = mapping(data.get(initial.command, {}), f"{path}: {initial.command}")
    children = command_parsers(selected)
    if children:
        child = initial.cache_action
        defaults.update({key: value for key, value in section.items() if key not in children})
        defaults.update(mapping(section.get(child, {}), f"{path}: {initial.command}.{child}"))
        selected = children[child]
        defaults = {**{k: v for k, v in shared.items() if k in options(selected)}, **defaults}
    else:
        # Command-level alternatives replace shared defaults in the same exclusive group.
        for group in selected._mutually_exclusive_groups:
            keys = {key for key, action in options(selected).items() if action in group._group_actions}
            if keys & section.keys():
                defaults = {key: value for key, value in defaults.items() if key not in keys}
        defaults.update(section)
    actions = options(selected)
    unknown = defaults.keys() - actions.keys()
    if unknown:
        raise ValueError(f"{path}: unknown {initial.command} flag(s): {', '.join(sorted(unknown))}")
    # Parse with suppressed defaults to identify explicit options, including -o and --flag=value.
    saved = [(action, action.default) for action in selected._actions]
    try:
        for action, _ in saved:
            action.default = argparse.SUPPRESS
        explicit = vars(parser.parse_args(argv))
    finally:
        for action, default in saved:
            action.default = default
    configured_groups = set()
    for key, value in defaults.items():
        action = actions[key]
        group = next((g for g in selected._mutually_exclusive_groups if action in g._group_actions), None)
        if action.dest in explicit or (group and any(a.dest in explicit for a in group._group_actions)):
            continue
        try:
            if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
                if type(value) is not bool:
                    raise ValueError("must be a boolean")
                converted = value if isinstance(action, argparse._StoreTrueAction) else not value
            elif isinstance(action, argparse._StoreConstAction):
                if type(value) is not bool:
                    raise ValueError("must be a boolean")
                if not value:
                    continue
                converted = action.const
            else:
                if isinstance(action, argparse._AppendAction):
                    if not isinstance(value, list):
                        raise TypeError("must be a list")
                    converted = [convert(item, action, path) for item in value]
                else:
                    converted = convert(value, action, path)
            if group:
                if group in configured_groups:
                    raise ValueError("mutually exclusive flags configured together")
                configured_groups.add(group)
            setattr(initial, action.dest, converted)
        except (ValueError, TypeError, argparse.ArgumentTypeError) as exc:
            raise ValueError(f"{path}: {key}: {exc}") from exc
    return initial


def convert(item, action, path):
    if action.type is boolean:
        result = boolean(item)
    else:
        if isinstance(item, (dict, list, bool)) or item is None:
            raise ValueError("invalid scalar value")
        if action.type is None and not isinstance(item, str):
            raise ValueError("must be a string")
        if (action.type is int or getattr(action.type, "__name__", "") == "nonnegative") and not isinstance(
            item, (int, str)
        ):
            raise ValueError("must be an integer")
        result = action.type(item) if action.type else item
    if action.choices is not None and result not in action.choices:
        raise ValueError(f"must be one of {', '.join(action.choices)}")
    if isinstance(result, Path) and result != Path("-"):
        result = result.expanduser()
        if not result.is_absolute():
            result = path.parent / result
    return result
