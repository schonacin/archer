"""Git snapshots read through git objects; never checkout or mutate the repository."""

import subprocess
from pathlib import Path

from archer.scan import ignore_spec, included, scan_sources


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], check=False, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def repository(root):
    return Path(git(root, "rev-parse", "--show-toplevel").decode().strip())


def snapshot(root, revision, **kwargs):
    root = repository(root)
    sources = {}
    ignore = ignore_spec(root)
    excludes = kwargs.get("excludes", ())
    if revision == "WORKTREE":
        paths = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
        for raw in sorted(set(paths)):
            path = raw.decode(errors="surrogateescape")
            p = root / path
            if included(path, excludes, ignore) and p.is_file() and not p.is_symlink():
                sources[path] = p.read_bytes()
        metadata = {"kind": "WORKTREE"}
    else:
        if revision == "INDEX":
            entries = git(root, "ls-files", "--stage", "-z").split(b"\0")
            metadata = {"kind": "INDEX"}
        else:
            sha = (
                git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
                .decode()
                .strip()
            )
            entries = git(root, "ls-tree", "-r", "-z", sha).split(b"\0")
            metadata = {"kind": "COMMIT", "revision": sha}
        blobs = []
        for entry in entries:
            if not entry:
                continue
            info, raw = entry.split(b"\t", 1)
            fields = info.split()
            path = raw.decode(errors="surrogateescape")
            if revision == "INDEX" and fields[2] != b"0":
                raise ValueError("Index contains unresolved merge conflicts")
            if included(path, excludes, ignore) and fields[0] in {b"100644", b"100755"}:
                blobs.append((path, fields[1] if revision == "INDEX" else fields[2]))
        if blobs:
            result = subprocess.run(
                ["git", "-C", str(root), "cat-file", "--batch"],
                input=b"\n".join(sha for _, sha in blobs) + b"\n",
                capture_output=True,
                check=True,
            )
            offset = 0
            for path, _ in blobs:
                end = result.stdout.index(b"\n", offset)
                size = int(result.stdout[offset:end].split()[-1])
                sources[path] = result.stdout[end + 1 : end + 1 + size]
                offset = end + size + 2
    return scan_sources(sources, snapshot=metadata, **kwargs)


def pair(root, revisions=(), mode=None, **kwargs):
    if revisions and (len(revisions) != 2 or mode):
        raise ValueError("Supply exactly two revisions, or one snapshot mode")
    if revisions:
        before, after = revisions
    else:
        before, after = {
            "staged": ("HEAD", "INDEX"),
            "unstaged": ("INDEX", "WORKTREE"),
            "worktree": ("HEAD", "WORKTREE"),
            None: ("HEAD", "WORKTREE"),
        }[mode]
    return snapshot(root, before, **kwargs), snapshot(root, after, **kwargs)
