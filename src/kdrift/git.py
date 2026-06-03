"""Git operations for baseline management.

All operations are read-only (no index locks). Worktrees use separate
indexes and are safe for concurrent use.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

import structlog

log: structlog.stdlib.BoundLogger = structlog.get_logger()


class GitError(Exception):
    """Raised when a git operation fails."""


def find_repo_root(start: Path | None = None) -> Path:
    """Find the git repository root from a starting directory."""
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=start)
    return Path(result.strip())


def resolve_ref(ref: str = "HEAD", repo_root: Path | None = None) -> str:
    """Resolve a git ref to its full SHA."""
    try:
        result = _run_git(["rev-parse", ref], cwd=repo_root)
        return result.strip()
    except GitError as e:
        msg = f"Cannot resolve ref '{ref}'"
        raise GitError(msg) from e


def get_short_sha(ref: str = "HEAD", repo_root: Path | None = None) -> str:
    """Get the short SHA for a ref."""
    result = _run_git(["rev-parse", "--short", ref], cwd=repo_root)
    return result.strip()


def changed_files(
    ref: str = "HEAD",
    paths: list[Path] | None = None,
    repo_root: Path | None = None,
) -> list[Path]:
    """Get files changed in the working tree relative to a ref.

    Args:
        ref: Git ref to compare against (default: HEAD).
        paths: Scope the diff to these paths only.
        repo_root: Repository root directory.

    Returns:
        List of changed file paths relative to the repo root.
    """
    cmd = ["diff", "--name-only", ref]
    if paths:
        cmd.append("--")
        cmd.extend(str(p) for p in paths)

    path_args = ["--", *(str(p) for p in paths)] if paths else []

    result = _run_git(cmd, cwd=repo_root)
    staged = _run_git(
        ["diff", "--name-only", "--cached", ref, *path_args],
        cwd=repo_root,
    )
    untracked = _run_git(
        ["ls-files", "--others", "--exclude-standard", *path_args],
        cwd=repo_root,
    )

    all_files: set[str] = set()
    for output in (result, staged, untracked):
        for line in output.strip().splitlines():
            if line:
                all_files.add(line)

    return sorted(Path(f) for f in all_files)


def changed_files_between(
    ref_a: str,
    ref_b: str,
    paths: list[Path] | None = None,
    repo_root: Path | None = None,
) -> list[Path]:
    """Get files changed between two refs.

    Args:
        ref_a: Base git ref.
        ref_b: Target git ref.
        paths: Scope the diff to these paths only.
        repo_root: Repository root directory.

    Returns:
        List of changed file paths relative to the repo root.
    """
    path_args = ["--", *(str(p) for p in paths)] if paths else []
    result = _run_git(
        ["diff", "--name-only", ref_a, ref_b, *path_args],
        cwd=repo_root,
    )

    return sorted(Path(line) for line in result.strip().splitlines() if line)


def has_commits(repo_root: Path | None = None) -> bool:
    """Check if the repo has any commits."""
    try:
        _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    except GitError:
        return False
    return True


class Worktree:
    """Context manager for a temporary git worktree.

    Creates a worktree at a unique temp path for the given ref, and
    removes it on exit. Worktrees have separate indexes so they don't
    lock the main repo.
    """

    def __init__(self, ref: str, repo_root: Path | None = None) -> None:
        """Create a worktree manager for the given ref."""
        self.ref = ref
        self.repo_root = repo_root or find_repo_root()
        self._worktree_path: Path | None = None

    @property
    def path(self) -> Path:
        """Path to the worktree directory."""
        if self._worktree_path is None:
            msg = "Worktree not created yet; use as a context manager"
            raise GitError(msg)
        return self._worktree_path

    def __enter__(self) -> Worktree:
        """Create the temporary worktree."""
        unique = uuid.uuid4().hex[:8]
        self._worktree_path = Path(tempfile.gettempdir()) / f"kdrift-wt-{unique}"
        _run_git(
            ["worktree", "add", "--detach", str(self._worktree_path), self.ref],
            cwd=self.repo_root,
        )
        log.debug("worktree_created", path=str(self._worktree_path), ref=self.ref)
        return self

    def __exit__(self, *_: object) -> None:
        """Remove the temporary worktree."""
        if self._worktree_path is not None:
            try:
                _run_git(
                    ["worktree", "remove", "--force", str(self._worktree_path)],
                    cwd=self.repo_root,
                )
                log.debug("worktree_removed", path=str(self._worktree_path))
            except GitError:
                log.warning("worktree_remove_failed", path=str(self._worktree_path))


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    """Run a git command and return stdout."""
    cmd = ["git", *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
    except subprocess.CalledProcessError as e:
        msg = f"git {' '.join(args)} failed: {e.stderr.strip()}"
        raise GitError(msg) from e
    except FileNotFoundError as e:
        msg = "git binary not found on PATH"
        raise GitError(msg) from e
    return result.stdout
