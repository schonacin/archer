"""Disposable, size-bounded extraction facts; repository resolution is never cached."""

import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from importlib import import_module, metadata
from pathlib import Path

from archer.graph.model import Edge, Graph, Node, validate_range
from archer.scan.facts import ParsedModuleFacts

FORMAT_VERSION = 2
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_AGE_SECONDS = 30 * 24 * 60 * 60
DATABASE = "facts.sqlite3"


def default_directory():
    if base := os.environ.get("XDG_CACHE_HOME"):
        return Path(base) / "archer"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "archer" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "archer"
    return Path.home() / ".cache" / "archer"


def backend_identity(name):
    """Invalidate on adapter/native changes, including editable development builds."""
    backend = import_module(f"archer.scan.{name}_backend")
    paths = [Path(__file__).with_name("facts.py"), Path(backend.__file__)]
    versions = [FORMAT_VERSION, sys.version, sys.implementation.name, name]
    if name == "rust":
        paths.append(Path(import_module("archer._native").__file__))
    else:
        versions.extend([metadata.version("libcst"), backend.MAX_CST_DEPTH, backend.MAX_CST_NODES])
    digest = hashlib.sha256(json.dumps(versions).encode())
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def cache_key(raw, module, file, identity):
    source_hash = hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode()).hexdigest()
    return hashlib.sha256(json.dumps([identity, source_hash, module, file]).encode()).hexdigest()


def encode(facts, fingerprint):
    return json.dumps(
        {"fingerprint": fingerprint, "facts": asdict(facts)},
        default=lambda value: sorted(value),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def decode(payload, module, file):
    """Validate disposable JSON before allowing it into the resolver."""
    data = json.loads(payload)
    fingerprint = data["fingerprint"]
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("Invalid fingerprint")
    int(fingerprint, 16)
    data = data["facts"]
    nodes = {k: Node(**n) for k, n in data["nodes"].items()}
    edges = [Edge(**e) for e in data["edges"]]
    placeholder = Node(module, "module", module, module, file, None)
    Graph({module: placeholder, **nodes}, edges).validate()
    for node in nodes.values():
        if node.module != module or node.file != file or node.kind not in {"class", "method", "function"}:
            raise ValueError("Invalid declaration")
    if any(e.kind != "contains" for e in edges):
        raise ValueError("Resolved edges do not belong in the extraction cache")

    def strings(values):
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError("Invalid name list")
        return set(values)

    pending = data["pending"]
    if not isinstance(pending, list):
        raise TypeError("Invalid references")
    for ref in pending:
        if ref["source"] not in nodes and ref["source"] != module:
            raise ValueError("Invalid reference source")
        if ref["kind"] not in {"calls", "imports", "inherits"}:
            raise ValueError("Invalid reference kind")
        if not all(isinstance(ref[k], str) for k in ("text", "identity")):
            raise ValueError("Invalid reference text")
        strings(ref["candidates"])
        validate_range(ref["range"])
        if ref["hint"] is not None and not isinstance(ref["hint"], str):
            raise ValueError("Invalid reference hint")
        if not isinstance(ref["receivers"], dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in ref["receivers"].items()
        ):
            raise ValueError("Invalid receivers")
    maps = []
    for key in ("exports", "attributes", "bindings"):
        if not isinstance(data[key], dict) or not all(isinstance(k, str) for k in data[key]):
            raise ValueError("Invalid bindings")
        maps.append({k: strings(v) for k, v in data[key].items()})
    direct = data["direct_fingerprints"]
    if not isinstance(direct, dict):
        raise TypeError("Invalid direct fingerprints")
    for ident, digest in direct.items():
        if not isinstance(ident, str) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Invalid direct fingerprint")
        int(digest, 16)
    return ParsedModuleFacts(nodes, edges, pending, *maps, strings(data["rebindings"]), direct), fingerprint


class FactsCache:
    def __init__(self, directory=None, max_bytes=DEFAULT_MAX_BYTES, *, max_age=MAX_AGE_SECONDS):
        self.directory = Path(directory).expanduser() if directory is not None else default_directory()
        self.max_bytes = max_bytes
        self.max_age = max_age
        self.connection = None
        if max_bytes < 64 * 1024:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.connection = sqlite3.connect(self.directory / DATABASE, timeout=0.05)
            self.connection.execute("PRAGMA auto_vacuum=FULL")
            # Disposable cache: keep rollback journals in memory, avoiding WAL
            # or journal files that could grow beyond the disk budget.
            self.connection.execute("PRAGMA journal_mode=MEMORY")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, version INTEGER NOT NULL, "
                "accessed REAL NOT NULL, checksum TEXT NOT NULL, payload BLOB NOT NULL)"
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS facts_accessed ON facts(accessed)")
            self.connection.commit()
            self.prune()
            page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
            # SQLite enforces the physical database limit, including indexes/overhead.
            self.connection.execute(f"PRAGMA max_page_count={max(1, max_bytes // page_size)}")
        except (OSError, sqlite3.Error):
            self.close()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def prune(self, incoming=0):
        db = self.connection
        if db is None:
            return
        with db:
            db.execute(
                "DELETE FROM facts WHERE accessed < ? OR version < ?",
                (time.time() - self.max_age, FORMAT_VERSION),
            )
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        size = db.execute("PRAGMA page_count").fetchone()[0] * page_size
        # Reserve page/index overhead when estimating the cost of an insertion.
        needed = incoming + 4 * page_size if incoming else 0
        if size + needed <= self.max_bytes:
            return
        target = max(0, int(self.max_bytes * 0.8) - needed)
        with db:
            for key, length in db.execute(
                "SELECT key, length(payload) FROM facts ORDER BY accessed, key"
            ).fetchall():
                if size <= target:
                    break
                db.execute("DELETE FROM facts WHERE key=?", (key,))
                size -= length
        # auto_vacuum=FULL reclaims deleted pages on commit, so evicted entries
        # do not leave an ever-growing database behind.

    def get(self, key, module, file):
        db = self.connection
        if db is None:
            return None
        try:
            row = db.execute(
                "SELECT checksum, payload FROM facts WHERE key=? AND version=? AND accessed>=? AND length(payload)<=?",
                (key, FORMAT_VERSION, time.time() - self.max_age, MAX_ENTRY_BYTES),
            ).fetchone()
            if row is None:
                return None
            checksum, payload = row
            if (
                len(payload) > MAX_ENTRY_BYTES
                or hashlib.sha256(key.encode() + payload).hexdigest() != checksum
            ):
                raise ValueError("Corrupt cache entry")
            result = decode(payload, module, file)
            with db:
                db.execute("UPDATE facts SET accessed=? WHERE key=?", (time.time(), key))
            return result
        except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            return None

    def put(self, key, facts, fingerprint):
        db = self.connection
        if db is None:
            return
        try:
            payload = encode(facts, fingerprint)
            if len(payload) > min(MAX_ENTRY_BYTES, self.max_bytes // 2):
                return
            self.prune(len(payload))
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO facts VALUES (?, ?, ?, ?, ?)",
                    (
                        key,
                        FORMAT_VERSION,
                        time.time(),
                        hashlib.sha256(key.encode() + payload).hexdigest(),
                        payload,
                    ),
                )
        except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError):
            # Locked/full/unwritable caches never make a scan incomplete.
            return


def cache_info(directory=None):
    directory = Path(directory).expanduser() if directory is not None else default_directory()
    path = directory / DATABASE
    report = {"directory": str(directory), "bytes": 0, "entries": 0, "payload_bytes": 0}
    if not path.exists():
        return report
    report["bytes"] = path.stat().st_size
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)
        try:
            report["entries"], report["payload_bytes"] = db.execute(
                "SELECT count(*), coalesce(sum(length(payload)), 0) FROM facts"
            ).fetchone()
        finally:
            db.close()
    except sqlite3.Error as exc:
        report["error"] = str(exc)
    return report


def clear_cache(directory=None):
    """Only clear Archer's named database, never unrelated directory contents."""
    directory = Path(directory).expanduser() if directory is not None else default_directory()
    path = directory / DATABASE
    if not path.exists():
        return
    db = sqlite3.connect(path, timeout=1)
    corrupt = False
    try:
        db.execute("PRAGMA journal_mode=MEMORY")
        with db:
            db.execute("DELETE FROM facts")
    except sqlite3.DatabaseError as exc:
        if getattr(exc, "sqlite_errorcode", None) not in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            raise
        corrupt = True
    finally:
        db.close()
    if corrupt:
        path.unlink(missing_ok=True)
