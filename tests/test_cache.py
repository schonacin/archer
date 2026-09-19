import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from archer.cli import main
from archer.scan import cache as cache_module
from archer.scan import scan_sources
from archer.scan.cache import FactsCache, backend_identity, cache_info, cache_key, clear_cache
from archer.scan.facts import ParsedModuleFacts

scanner = importlib.import_module("archer.scan")


def facts(size=10):
    return ParsedModuleFacts(
        pending=[
            {
                "source": "a",
                "kind": "calls",
                "text": "x" * size,
                "identity": "0" * 64,
                "range": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 1}},
                "candidates": [],
                "receivers": {},
                "hint": None,
            }
        ]
    )


@pytest.mark.parametrize("backend", ["rust", "libcst"])
def test_hits_skip_extraction_and_fingerprints(tmp_path, monkeypatch, backend):
    sources = {"a.py": "def f(): pass\ndef g(): f()"}
    cold = scan_sources(sources, parser=backend, cache_dir=tmp_path)

    def unexpected(*args, **kwargs):
        pytest.fail("A cache hit must not extract or tokenize again")

    monkeypatch.setattr(scanner, "get_parser", lambda name: unexpected)
    monkeypatch.setattr(scanner, "fingerprint", unexpected)
    monkeypatch.setattr(scanner, "direct_fingerprints", unexpected)
    warm = scan_sources(sources, parser=backend, cache_dir=tmp_path)
    assert warm.to_dict() == cold.to_dict()
    assert cache_info(tmp_path)["entries"] == 1


def test_key_tracks_full_source_context_and_backend():
    base = cache_key("x=1", "a", "a.py", "rust-v1")
    variants = [
        ("# comment\nx=1", "a", "a.py", "rust-v1"),
        ("x = 1", "a", "a.py", "rust-v1"),
        ("x=1", "b", "a.py", "rust-v1"),
        ("x=1", "a", "b.py", "rust-v1"),
        ("x=1", "a", "a.py", "rust-v2"),
    ]
    assert all(cache_key(*variant) != base for variant in variants)
    assert backend_identity("rust") != backend_identity("libcst")


def test_independent_objects_and_positions(tmp_path):
    sources = {"a.py": "def f(): pass\nf()"}
    original = scan_sources(sources, cache_dir=tmp_path)
    expected = original.to_json()
    original.nodes["a.f"].metadata["fingerprint"] = "changed"
    original.edges.clear()
    assert scan_sources(sources, cache_dir=tmp_path).to_json() == expected
    changed = scan_sources({"a.py": "\n" + sources["a.py"]}, cache_dir=tmp_path)
    assert changed.nodes["a.f"].source_range["start"]["line"] == 2
    assert cache_info(tmp_path)["entries"] == 2


def test_no_cache_performs_no_disk_io(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Disabled cache accessed disk")

    monkeypatch.setattr(scanner, "backend_identity", unexpected)
    for options in ({"cache": False}, {"cache_max_mb": 0}):
        graph = scan_sources({"a.py": "pass"}, cache_dir=tmp_path / "unused", **options)
        assert not graph.metadata["diagnostics"]
    assert not (tmp_path / "unused").exists()


def test_lru_eviction_and_physical_bound(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cache_module.time, "time", lambda: clock[0])
    cache = FactsCache(tmp_path, 128 * 1024)
    try:
        for key in ("cold", "hot", "new"):
            cache.put(key, facts(24000), "0" * 64)
            clock[0] += 1
        assert cache.get("hot", "a", "a.py") is not None
        clock[0] += 1
        cache.put("incoming", facts(24000), "0" * 64)
        assert cache.get("cold", "a", "a.py") is None
        assert cache.get("hot", "a", "a.py") is not None
        assert cache.get("incoming", "a", "a.py") is not None
        assert cache_info(tmp_path)["bytes"] <= 128 * 1024
        for i in range(30):
            clock[0] += 1
            cache.put(str(i), facts(24000), "0" * 64)
            assert cache_info(tmp_path)["bytes"] <= 128 * 1024
    finally:
        cache.close()


def test_expiry_and_obsolete_formats(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cache_module.time, "time", lambda: clock[0])
    cache = FactsCache(tmp_path, max_age=30)
    cache.put("old", facts(), "0" * 64)
    clock[0] = 131.0
    assert cache.get("old", "a", "a.py") is None
    cache.prune()
    assert cache_info(tmp_path)["entries"] == 0
    cache.put("obsolete", facts(), "0" * 64)
    cache.close()
    monkeypatch.setattr(cache_module, "FORMAT_VERSION", cache_module.FORMAT_VERSION + 1)
    cache = FactsCache(tmp_path)
    cache.close()
    assert cache_info(tmp_path)["entries"] == 0


def test_size_reduction_reclaims_disk(tmp_path):
    cache = FactsCache(tmp_path, 512 * 1024)
    for i in range(12):
        cache.put(str(i), facts(20000), "0" * 64)
    cache.close()
    assert cache_info(tmp_path)["bytes"] > 128 * 1024
    cache = FactsCache(tmp_path, 128 * 1024)
    cache.close()
    assert cache_info(tmp_path)["bytes"] <= 128 * 1024


def test_oversized_entry_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "MAX_ENTRY_BYTES", 1024)
    cache = FactsCache(tmp_path)
    cache.put("large", facts(2000), "0" * 64)
    cache.close()
    assert cache_info(tmp_path)["entries"] == 0


@pytest.mark.parametrize("corruption", ["payload", "shape", "database", "unwritable"])
def test_cache_failure_is_a_miss(tmp_path, corruption):
    sources = {"a.py": "def f(): pass\nf()"}
    expected = scan_sources(sources, cache=False).to_json()
    scan_sources(sources, cache_dir=tmp_path)
    path = tmp_path / cache_module.DATABASE
    if corruption == "database":
        path.write_bytes(b"not a database")
    elif corruption == "unwritable":
        # A file where a directory is expected works even when tests run as root.
        tmp_path = tmp_path / "blocked"
        tmp_path.write_text("not a directory")
    else:
        with sqlite3.connect(path) as db:
            payload = b"{}" if corruption == "shape" else b"broken json"
            checksum = (
                cache_module.hashlib.sha256(
                    db.execute("SELECT key FROM facts").fetchone()[0].encode() + payload
                ).hexdigest()
                if corruption == "shape"
                else "invalid"
            )
            db.execute("UPDATE facts SET payload=?, checksum=?", (payload, checksum))
    result = scan_sources(sources, cache_dir=tmp_path)
    assert result.to_json() == expected
    assert not result.metadata["diagnostics"]


def test_locked_cache_is_optional(tmp_path):
    expected = scan_sources({"a.py": "pass"}, cache_dir=tmp_path).to_json()
    db = sqlite3.connect(tmp_path / cache_module.DATABASE)
    try:
        db.execute("BEGIN EXCLUSIVE")
        assert scan_sources({"a.py": "pass"}, cache_dir=tmp_path).to_json() == expected
    finally:
        db.rollback()
        db.close()


def test_concurrent_writers(tmp_path):
    def write(i):
        cache = FactsCache(tmp_path, 128 * 1024)
        try:
            cache.put(str(i), facts(8000), "0" * 64)
        finally:
            cache.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(32)))
    info = cache_info(tmp_path)
    assert info["entries"] > 0
    assert info["bytes"] <= 128 * 1024
    assert "error" not in info


def test_failed_files_are_not_cached(tmp_path):
    graph = scan_sources({"bad.py": "def broken(:"}, cache_dir=tmp_path)
    assert graph.metadata["diagnostics"]
    assert cache_info(tmp_path)["entries"] == 0


def test_cache_commands_and_clear_scope(tmp_path, capsys):
    directory = tmp_path / "custom"
    assert main(["cache", "info", "--cache-dir", str(directory), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == 0
    assert not directory.exists()
    scan_sources({"a.py": "pass"}, cache_dir=directory)
    keep = directory / "unrelated.txt"
    keep.write_text("keep")
    assert main(["cache", "clear", "--cache-dir", str(directory), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == 0
    assert keep.read_text() == "keep"


def test_cli_cache_configuration(tmp_path, capsys):
    (tmp_path / "a.py").write_text("def f(): pass")
    (tmp_path / "pyproject.toml").write_text('[tool.archer]\ncache_dir="custom-cache"\ncache_max_mb=1\n')
    args = ["scan", "--root", str(tmp_path), "-o", "-"]
    assert main([*args, "--no-cache"]) == 0
    assert not (tmp_path / "custom-cache").exists()
    capsys.readouterr()
    assert main(args) == 0
    assert cache_info(tmp_path / "custom-cache")["entries"] == 1
    expected = json.loads(capsys.readouterr().out)
    assert main([*args, "--cache-dir", str(tmp_path / "override"), "--cache-max-mb", "0"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert not (tmp_path / "override").exists()


def test_clear_recovers_corrupt_database(tmp_path):
    (tmp_path / cache_module.DATABASE).write_bytes(b"not sqlite")
    clear_cache(tmp_path)
    scan_sources({"a.py": "pass"}, cache_dir=tmp_path)
    assert cache_info(tmp_path)["entries"] == 1
