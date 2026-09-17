"""Select an extraction backend without importing the other implementation."""

from importlib import import_module

from archer.scan.facts import ExtractionLimitError, ParsedModuleFacts, fingerprint

__all__ = ["ExtractionLimitError", "ParsedModuleFacts", "fingerprint", "get_parser"]
PARSERS = ("rust", "libcst")


def get_parser(name="rust"):
    if name not in PARSERS:
        raise ValueError(f"Unknown parser {name!r}; choose rust or libcst")
    try:
        backend = import_module(f"archer.scan.{name}_backend")
        return backend.extract
    except ImportError as exc:
        hint = (
            "rebuild/install Archer's native extension or select --parser libcst"
            if name == "rust"
            else "install archer[libcst]"
        )
        raise ValueError(f"The {name} parser is unavailable; {hint}") from exc
