"""Independent review: after verification, a separate reviewer session (same tool, fresh
context, read-only) checks the task's own diff against spec and contract before it counts
(Kiro Crew: TaskRunner self-review of the actual diff)."""

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.errors import AosError
from aos.mcp_server import BoardTools, Tools, build_server

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


@pytest.fixture
def board(data):
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "a", max_attempts=5)
    b.promote()
    b.claim(t)
    b.complete(t, "claimed", author="w", to="review")
    return b, t


def test_board_review_flow(board):
    b, t = board
    b.mark_verified(t, "verified: 1 new commit(s)")
    assert b.task(t)["review_stage"] == "verified"
    task = b.start_review(t)
    assert task["review_stage"] == "reviewing" and task["generation"] == 2
    with pytest.raises(AosError, match="stale"):
        b.review_verdict(t, True, "ok", generation=1)       # the worker's token can't review
    b.review_verdict(t, True, "spec met", generation=2)
    assert b.task(t)["status"] == "done"
    assert any(e["kind"] == "reviewed" for e in b.events("checkout"))


def test_failed_review_goes_back_with_findings(board):
    b, t = board
    b.mark_verified(t, "verified")
    g = b.start_review(t)["generation"]
    b.review_verdict(t, False, "missing test for the empty cart", generation=g)
    task = b.task(t)
    assert task["status"] == "ready" and "empty cart" in task["feedback"] and task["review_stage"] is None


def test_reviewer_without_verdict_twice_is_accepted_visibly(board):
    b, t = board
    b.mark_verified(t, "verified")
    b.start_review(t)
    b.review_unfinished(t, "reviewer exited without a verdict")
    assert b.task(t)["status"] == "review" and b.task(t)["review_stage"] == "verified"
    b.start_review(t)
    b.review_unfinished(t, "reviewer exited without a verdict")
    assert b.task(t)["status"] == "done"
    assert any(e["kind"] == "review_skipped" for e in b.events("checkout"))


def test_reviewer_mcp_tools(configured, board):
    b, t = board
    AOS(configured).register_project("api")
    b.mark_verified(t, "verified")
    g = b.start_review(t)["generation"]
    rv = BoardTools(AOS(configured, slug="api"), task_id=t, attempt=g, review=True)
    names = {x.name for x in asyncio.run(build_server(Tools(rv.aos), rv).list_tools())}
    assert {"review_pass", "review_fail", "task_show", "board_read"} <= names
    assert not {"task_complete", "task_block", "task_create"} & names
    assert rv.review_fail("off by one in total")["status"] == "ready"


# -- dispatcher ----------------------------------------------------------------
@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
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
    return configured, b


def test_reviewed_task_is_done(env, monkeypatch):
    repo, b = env
    monkeypatch.setenv("FAKE_REVIEW", "pass")
    t = b.add_task("checkout", "api", "a", worker="fake")
    Dispatcher(repo, tick=0.05).run(once=True)
    kinds = [e["kind"] for e in b.events("checkout") if e["task"] == t]
    assert b.task(t)["status"] == "done" and "verified" in kinds and "reviewed" in kinds


def test_failed_review_sends_findings_to_the_next_attempt(env, monkeypatch):
    repo, b = env
    monkeypatch.setenv("FAKE_REVIEW", "fail")
    t = b.add_task("checkout", "api", "a", worker="fake", max_attempts=5)
    Dispatcher(repo, tick=0.05).run(once=True)
    task = b.task(t)
    assert task["status"] == "ready" and "ignores the discount" in task["feedback"]


def test_review_prompt_and_context(env, tmp_path):
    from aos.workers.common import prepare_review
    repo, b = env
    t = b.add_task("checkout", "api", "Orders endpoint", spec="POST /orders with discounts", worker="fake")
    b.promote()
    b.claim(t, base_commit="HEAD")
    b.complete(t, "added discount handling", author="w", to="review")
    b.mark_verified(t, "verified")
    task = b.start_review(t)
    proj = Path(load_user_config()["projects"]["api"]["path"])
    spec, settings = prepare_review(AOS(repo, slug="api"), b, task, proj, proj, "/bin/aos")
    ctx = spec.context_file.read_text()
    assert "independent reviewer" in ctx.lower() and "POST /orders with discounts" in ctx
    assert "added discount handling" in ctx
    assert "write" not in spec.allowed and "shell:git commit" not in spec.allowed
    assert "--review" in spec.mcp_servers["aos"]["args"]
