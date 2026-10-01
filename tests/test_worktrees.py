import subprocess

import pytest

from aos.errors import AosError
from aos.worktrees import branch_name, ensure_worktree, worktree_path


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_paths(tmp_path):
    assert worktree_path(tmp_path, "checkout", "web") == tmp_path / "worktrees/checkout/web"
    assert branch_name("checkout") == "feature/checkout"


def test_creates_and_reuses(tmp_path, git_repo):
    proj = git_repo(tmp_path / "shop")
    wt = tmp_path / "wts/checkout/shop"
    assert ensure_worktree(proj, wt, "feature/checkout") == wt
    assert (wt / "README.md").exists()
    assert git(wt, "branch", "--show-current") == "feature/checkout"
    assert git(proj, "branch", "--show-current") == "main"
    (wt / "new.txt").write_text("x")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "work")
    assert ensure_worktree(proj, wt, "feature/checkout") == wt
    assert not (proj / "new.txt").exists()
    assert git(proj, "status", "--porcelain") == ""


def test_recreate_keeps_branch_commits(tmp_path, git_repo):
    proj = git_repo(tmp_path / "shop")
    wt = tmp_path / "wt"
    ensure_worktree(proj, wt, "feature/checkout")
    (wt / "new.txt").write_text("x")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "work")
    git(proj, "worktree", "remove", str(wt))
    ensure_worktree(proj, wt, "feature/checkout")
    assert (wt / "new.txt").exists()


def test_refusals(tmp_path, git_repo):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(AosError, match="not a git"):
        ensure_worktree(plain, tmp_path / "wt1", "feature/x")
    proj = git_repo(tmp_path / "shop")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "f").write_text("")
    with pytest.raises(AosError, match="not a worktree"):
        ensure_worktree(proj, occupied, "feature/x")
