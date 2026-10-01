"""Git worktrees for feature work: <home>/worktrees/<feature>/<project> on feature/<feature>.

The user's own checkout and current branch are never touched; an existing
feature branch is reused, never reset.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import AosError


def worktree_path(home: str | Path, feature: str, project: str) -> Path:
    return Path(home) / "worktrees" / feature / project


def branch_name(feature: str) -> str:
    return f"feature/{feature}"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _common_dir(cwd: Path) -> Path | None:
    r = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 else None


def ensure_worktree(project: str | Path, path: str | Path, branch: str) -> Path:
    project, path = Path(project).resolve(), Path(path)
    common = _common_dir(project) if project.is_dir() else None
    if common is None:
        raise AosError(f"{project} is not a git repository", "board worktrees need git: git init + one commit")
    if path.exists():
        if _common_dir(path) != common:
            raise AosError(f"{path} exists but is not a worktree of {project}", "move it away, then retry the task")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], project).returncode == 0
    args = ["worktree", "add", str(path), branch] if exists else ["worktree", "add", "-b", branch, str(path)]
    r = _git(args, project)
    if r.returncode != 0:
        raise AosError(f"git worktree add failed: {r.stderr.strip()}", "the repo needs at least one commit")
    return path
