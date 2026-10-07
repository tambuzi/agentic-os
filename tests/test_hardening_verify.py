"""Verify claims: task_complete is a claim, not a fact. The dispatcher checks the task
added commits and that the project's verify_command passes before it counts as done
(Kiro Crew: pipeline conductor's independent verification)."""

import sys
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.mcp_server import BoardTools

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


# -- board level ---------------------------------------------------------------
@pytest.fixture
def board(data):
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    a = b.add_task("checkout", "api", "a", max_attempts=5)
    d = b.add_task("checkout", "web", "b", depends_on=[a])
    b.promote()
    return b, a, d


def test_complete_to_review_then_accept(board):
    b, a, d = board
    b.claim(a, base_commit="abc123")
    assert b.task(a)["base_commit"] == "abc123"
    b.complete(a, "done", author="w", to="review")
    assert b.task(a)["status"] == "review" and b.promote() == []  # dependents wait for acceptance
    b.accept(a, "verified: 1 commit")
    assert b.task(a)["status"] == "done" and b.promote() == [d]


def test_rejected_review_requeues_with_feedback(board):
    b, a, _ = board
    b.claim(a)
    b.complete(a, "done", author="w", to="review")
    b.reject_review(a, "no commits since the task started")
    t = b.task(a)
    assert t["status"] == "ready" and t["feedback"] == "no commits since the task started"
    b.claim(a)
    b.complete(a, "done again", author="w", to="review")
    b.reject_review(a, "no commits since the task started")
    assert b.task(a)["status"] == "failed"  # same failure twice


# -- MCP: a worker's task_complete is a claim ----------------------------------
def test_worker_complete_goes_to_review_unless_disabled(configured, data):
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "a")
    b.promote()
    b.claim(t)
    w = BoardTools(AOS(configured, slug="api"), task_id=t)
    assert w.task_complete("done")["status"] == "review"
    (configured / "aos.yaml").write_text("board:\n  verify: false\n")
    u = b.add_task("checkout", "api", "u")
    b.promote()
    b.claim(u)
    assert BoardTools(AOS(configured, slug="api"), task_id=u).task_complete("done")["status"] == "done"


# -- dispatcher ----------------------------------------------------------------
@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"default_worker": "fake", "review": False},  # verification alone; review has its own tests
        "workers": {"fake": {"adapter": "command", "timeout_min": 0.1,
                             "command": [sys.executable, str(FAKE), "{task_id}"]}}}))
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    return configured, b, data


def set_verify(data, command):
    p = data / "projects.yaml"
    d = yaml.safe_load(p.read_text())
    d["projects"]["api"]["worker"] = {"verify_command": command}
    p.write_text(yaml.safe_dump(d))


def test_committed_work_is_verified_and_done(env, monkeypatch):
    repo, b, _ = env
    monkeypatch.setenv("FAKE_MODE", "commit_complete")
    t = b.add_task("checkout", "api", "a", worker="fake")
    Dispatcher(repo, tick=0.05).run(once=True)
    task = b.task(t)
    assert task["status"] == "done" and task["base_commit"]
    assert any(e["kind"] == "verified" for e in b.events("checkout"))


def test_claim_without_commits_is_rejected(env, monkeypatch):
    repo, b, _ = env
    monkeypatch.setenv("FAKE_MODE", "nocommit_complete")
    t = b.add_task("checkout", "api", "a", worker="fake", max_attempts=5)
    Dispatcher(repo, tick=0.05).run(once=True)
    task = b.task(t)
    assert task["status"] == "ready" and "no commits" in task["feedback"]
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(t)["status"] == "failed"


def test_verify_command_must_pass(env, monkeypatch):
    repo, b, data = env
    monkeypatch.setenv("FAKE_MODE", "commit_complete")
    set_verify(data, f"{sys.executable} -c \"print('3 tests failed'); raise SystemExit(1)\"")
    t = b.add_task("checkout", "api", "a", worker="fake", max_attempts=5)
    Dispatcher(repo, tick=0.05).run(once=True)
    task = b.task(t)
    assert task["status"] == "ready" and "3 tests failed" in task["feedback"] and "exit 1" in task["feedback"]
    set_verify(data, f"{sys.executable} -c \"print('ok')\"")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(t)["status"] == "done"


def test_next_attempt_prompt_carries_the_feedback(env, monkeypatch, tmp_path):
    from aos.workers.common import prepare_run
    repo, b, _ = env
    monkeypatch.setenv("FAKE_MODE", "nocommit_complete")
    t = b.add_task("checkout", "api", "a", worker="fake", max_attempts=5)
    Dispatcher(repo, tick=0.05).run(once=True)
    b.claim(t)
    spec, _ = prepare_run(AOS(repo, slug="api"), b, b.task(t), tmp_path / "wt", tmp_path, "/bin/aos")
    assert "not accepted" in spec.prompt and "no commits" in spec.prompt
