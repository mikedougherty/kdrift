"""Tests for git operations."""

import subprocess
from pathlib import Path

import pytest

from kdrift import git


@pytest.fixture()
def git_repo(tmp_path):
    """Create a minimal git repo with one commit."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True,
        check=True,
    )
    f = tmp_path / "file.txt"
    f.write_text("initial\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "file.txt"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "initial"],
        capture_output=True,
        check=True,
    )
    return tmp_path


@pytest.mark.unit
class TestFindRepoRoot:
    def test_finds_root(self, git_repo):
        sub = git_repo / "sub"
        sub.mkdir()
        root = git.find_repo_root(sub)
        assert root == git_repo

    def test_not_a_repo(self, tmp_path):
        with pytest.raises(git.GitError):
            git.find_repo_root(tmp_path)


@pytest.mark.unit
class TestResolveRef:
    def test_resolves_head(self, git_repo):
        sha = git.resolve_ref("HEAD", git_repo)
        assert len(sha) == 40

    def test_invalid_ref(self, git_repo):
        with pytest.raises(git.GitError):
            git.resolve_ref("nonexistent-ref-12345", git_repo)


@pytest.mark.unit
class TestChangedFiles:
    def test_detects_modified_file(self, git_repo):
        (git_repo / "file.txt").write_text("modified\n")
        changed = git.changed_files("HEAD", repo_root=git_repo)
        assert any("file.txt" in str(f) for f in changed)

    def test_detects_untracked_file(self, git_repo):
        (git_repo / "new.txt").write_text("new\n")
        changed = git.changed_files("HEAD", repo_root=git_repo)
        assert any("new.txt" in str(f) for f in changed)

    def test_no_changes(self, git_repo):
        changed = git.changed_files("HEAD", repo_root=git_repo)
        assert changed == []

    def test_scoped_to_path(self, git_repo):
        sub = git_repo / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("new\n")
        (git_repo / "root.txt").write_text("also new\n")
        changed = git.changed_files("HEAD", paths=[Path("sub")], repo_root=git_repo)
        assert any("a.txt" in str(f) for f in changed)
        assert not any("root.txt" in str(f) for f in changed)


@pytest.mark.unit
class TestHasCommits:
    def test_has_commits(self, git_repo):
        assert git.has_commits(git_repo)

    def test_no_commits(self, tmp_path):
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
        assert not git.has_commits(tmp_path)


@pytest.mark.unit
class TestWorktree:
    def test_creates_and_removes_worktree(self, git_repo):
        ref = git.resolve_ref("HEAD", git_repo)
        with git.Worktree(ref, git_repo) as wt:
            assert wt.path.is_dir()
            assert (wt.path / "file.txt").is_file()
            worktree_path = wt.path
        assert not worktree_path.is_dir()

    def test_path_not_accessible_before_enter(self, git_repo):
        wt = git.Worktree("HEAD", git_repo)
        with pytest.raises(git.GitError):
            _ = wt.path
