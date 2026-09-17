import subprocess

import pytest

from archer.git import pair, snapshot
from archer.graph.diff import diff


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.py").write_text("def first(): pass\n")
    (tmp_path / "gone.py").write_text("import a\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "initial")
    return tmp_path


@pytest.mark.parametrize("parser", ["rust", "libcst"])
def test_commit_index_worktree_are_distinct_and_readonly(repo, parser):
    (repo / "a.py").write_text("def staged(): pass\n")
    git(repo, "add", "a.py")
    (repo / "a.py").write_text("def unstaged(): pass\n")
    (repo / "new.py").write_text("import a\n")
    (repo / "gone.py").unlink()
    status = git(repo, "status", "--porcelain")
    head, index = pair(repo, mode="staged", parser=parser)
    index2, worktree = pair(repo, mode="unstaged", parser=parser)
    assert "a.first" in head.nodes
    assert "a.staged" in index.nodes and index.to_json() == index2.to_json()
    assert "a.unstaged" in worktree.nodes and "new" in worktree.nodes
    assert "gone" not in worktree.nodes
    assert diff(head, worktree).nodes["gone"].metadata["change"] == "removed"
    assert git(repo, "status", "--porcelain") == status
    assert {
        head.metadata["snapshot"]["kind"],
        index.metadata["snapshot"]["kind"],
        worktree.metadata["snapshot"]["kind"],
    } == {"COMMIT", "INDEX", "WORKTREE"}


def test_revision_pair_and_ignored_files(repo):
    (repo / "space name.py").write_text("x=1")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "second")
    before, after = pair(repo, ["HEAD~1", "HEAD"])
    assert "space name" not in before.nodes and "space name" in after.nodes
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / "ignored.py").write_text("x=1")
    assert "ignored" not in snapshot(repo, "WORKTREE").nodes
    with pytest.raises(ValueError):
        snapshot(repo, "--invalid")


def test_deep_file_diagnostics_across_snapshots(repo):
    (repo / "bad.py").write_text("x = " + "+".join(["x"] * 600))
    git(repo, "add", "bad.py")
    git(repo, "commit", "-m", "deep expression")
    for revision in ("HEAD", "INDEX", "WORKTREE"):
        graph = snapshot(repo, revision)
        assert "a.first" in graph.nodes
        assert graph.metadata["diagnostics"][0]["file"] == "bad.py"
        assert graph.metadata["diagnostics"][0]["stage"] == "complexity"
        graph.validate()


@pytest.mark.parametrize("parser", ["rust", "libcst"])
def test_cache_reuses_callers_but_resolves_each_snapshot(repo, tmp_path_factory, monkeypatch, parser):
    import importlib

    from archer.scan.cache import cache_info

    directory = tmp_path_factory.mktemp("git-facts")
    (repo / "old.py").write_text("class Old:\n def run(self): pass\n")
    (repo / "new.py").write_text("class New:\n def run(self): pass\n")
    (repo / "facade.py").write_text("from old import Old as Base\n")
    (repo / "caller.py").write_text(
        "from facade import Base\nclass Child(Base):\n def work(self): self.run()\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "old base")
    first = git(repo, "rev-parse", "HEAD")
    before = snapshot(repo, first, parser=parser, cache_dir=directory)
    assert any(e.source == "caller.Child.work" and e.target == "old.Old.run" for e in before.edges)
    (repo / "facade.py").write_text("from new import New as Base\n")
    git(repo, "add", "facade.py")
    git(repo, "commit", "-m", "new base; unchanged caller")

    scanner = importlib.import_module("archer.scan")
    original = scanner.get_parser
    extracted = []

    def tracking(name):
        extract = original(name)

        def call(source, module, file):
            extracted.append(file)
            return extract(source, module, file)

        return call

    monkeypatch.setattr(scanner, "get_parser", tracking)
    after = snapshot(repo, "HEAD", parser=parser, cache_dir=directory)
    assert extracted == ["facade.py"]
    assert any(e.source == "caller.Child.work" and e.target == "new.New.run" for e in after.edges)
    entries = cache_info(directory)["entries"]

    # Index and worktree share unchanged blobs while reflecting renames/deletions.
    git(repo, "mv", "caller.py", "renamed.py")
    (repo / "gone.py").unlink()
    status = git(repo, "status", "--porcelain")
    for mode in ("staged", "unstaged", "worktree"):
        cached = pair(repo, mode=mode, parser=parser, cache_dir=directory)
        uncached = pair(repo, mode=mode, parser=parser, cache=False)
        assert [g.to_dict() for g in cached] == [g.to_dict() for g in uncached]
        assert diff(*cached).to_dict() == diff(*uncached).to_dict()
    assert cache_info(directory)["entries"] > entries
    assert git(repo, "status", "--porcelain") == status
    assert (
        diff(before, after).to_dict()
        == diff(
            snapshot(repo, first, parser=parser, cache=False),
            snapshot(repo, "HEAD", parser=parser, cache=False),
        ).to_dict()
    )
