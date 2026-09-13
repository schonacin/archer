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


def test_commit_index_worktree_are_distinct_and_readonly(repo):
    (repo / "a.py").write_text("def staged(): pass\n")
    git(repo, "add", "a.py")
    (repo / "a.py").write_text("def unstaged(): pass\n")
    (repo / "new.py").write_text("import a\n")
    (repo / "gone.py").unlink()
    status = git(repo, "status", "--porcelain")
    head, index = pair(repo, mode="staged")
    index2, worktree = pair(repo, mode="unstaged")
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
