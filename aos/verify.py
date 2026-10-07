"""Verify a worker's claim before it counts (Kiro Crew: the pipeline conductor verifies
claimed results independently instead of trusting them).

A `task_complete` is a claim. The dispatcher checks, in the task's worktree:
- the attempt added at least one commit since it started (`base_commit`);
- the project's optional `verify_command` (e.g. `npm test`) passes.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def head_commit(worktree: Path) -> str | None:
    r = _git(["rev-parse", "HEAD"], Path(worktree))
    return r.stdout.strip() or None if r.returncode == 0 else None


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def verify_claim(task: dict, worktree: Path, verify_command: str | list | None,
                 timeout_sec: float = 600) -> tuple[bool, str]:
    """(ok, detail). `detail` explains a rejection well enough for the next attempt to act on."""
    worktree = Path(worktree)
    commits = None
    if task.get("base_commit"):
        r = _git(["rev-list", "--count", f"{task['base_commit']}..HEAD"], worktree)
        if r.returncode != 0:
            return False, f"cannot inspect the task's commits: {r.stderr.strip()}"
        commits = int(r.stdout.strip() or 0)
        if commits == 0:
            return False, ("no commits since the task started: commit your work (with its tests) on the "
                           "feature branch before calling task_complete")
    shown = ""
    if verify_command:
        argv = shlex.split(verify_command) if isinstance(verify_command, str) else [str(a) for a in verify_command]
        shown = verify_command if isinstance(verify_command, str) else " ".join(argv)
        try:
            r = subprocess.run(argv, cwd=worktree, capture_output=True, text=True, timeout=timeout_sec,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, f"verification command `{shown}` timed out after {timeout_sec:g}s"
        except OSError as e:
            return False, f"verification command `{shown}` could not start: {e}"
        if r.returncode != 0:
            return False, (f"verification command `{shown}` failed (exit {r.returncode}):\n"
                           f"{_tail((r.stdout or '') + (r.stderr or ''))}")
    parts = [f"verified: {commits} new commit(s)" if commits is not None else "verified"]
    if shown:
        parts.append(f"`{shown}` passed")
    return True, "; ".join(parts)
