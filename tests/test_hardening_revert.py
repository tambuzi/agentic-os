"""Revert on rejection: when verification or the reviewer rejects an attempt, the next
attempt starts from the attempt's base commit, not on top of the rejected work
(Kiro Crew: TaskRunner reverts a step whose review failed before retrying)."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_rejection_records_where_to_revert(data):
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "a", max_attempts=5)
    b.promote()
    b.claim(t, base_commit="abc123")
    b.complete(t, "claimed", author="w", to="review")
    b.reject_review(t, "tests fail")
    assert b.task(t)["revert_to"] == "abc123"
    b.claim(t, base_commit="abc123")
    assert b.task(t)["revert_to"] is None


@pytest.fixture
def env(configured, data, home, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("FAKE_MARKER_DIR", str(tmp_path / "markers"))
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"default_worker": "fake", "review": True},
        "workers": {"fake": {"adapter": "command", "timeout_min": 0.1,
                             "command": [sys.executable, str(FAKE), "{task_id}", "{role}"]}}}))
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    monkeypatch.setenv("FAKE_MODE", "commit_complete")
    return configured, b, home / "worktrees/checkout/api"


def test_failed_review_is_reverted_before_the_next_attempt(env, monkeypatch):
    repo, b, wt = env
    monkeypatch.setenv("FAKE_REVIEW", "fail_once")
    t = b.add_task("checkout", "api", "a", worker="fake", max_attempts=5)
    Dispatcher(repo, tick=0.05).run(once=True)        # attempt 1: commit, verified, review fails
    assert b.task(t)["status"] == "ready"
    rejected_head = git(wt, "rev-parse", "HEAD")
    first_attempt_scratch = sorted(p.name for p in wt.glob("scratch-*.tmp"))
    assert first_attempt_scratch
    base = b.task(t)["revert_to"]
    assert rejected_head != base
    Dispatcher(repo, tick=0.05).run(once=True)        # attempt 2 starts from base, review passes
    assert b.task(t)["status"] == "done"
    ancestors = git(wt, "log", "--format=%H")
    assert rejected_head not in ancestors                       # the rejected commit left the branch
    assert len([l for l in git(wt, "log", "--oneline", f"{base}..HEAD").splitlines()]) == 1
    left = {p.name for p in wt.glob("scratch-*.tmp")}
    assert not left & set(first_attempt_scratch)              # attempt 1's untracked leftovers cleaned
    rev = [e for e in b.events("checkout") if e["kind"] == "reverted"]
    assert rev and rejected_head[:12] in rev[0]["body"]       # old HEAD recorded for recovery


def test_no_reset_when_history_was_rewritten(env, monkeypatch):
    repo, b, wt = env
    monkeypatch.setenv("FAKE_REVIEW", "fail")
    t = b.add_task("checkout", "api", "a", worker="fake", max_attempts=5)
    Dispatcher(repo, tick=0.05).run(once=True)
    with Board(b.data)._tx() as c:   # pretend the base is not an ancestor any more
        c.execute("UPDATE tasks SET revert_to=? WHERE id=?", ("0" * 40, t))
    head = git(wt, "rev-parse", "HEAD")
    monkeypatch.setenv("FAKE_MODE", "noop")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert git(wt, "rev-parse", "HEAD") == head
    assert any(e["kind"] == "status" and "not reverted" in e["body"] for e in b.events("checkout"))
