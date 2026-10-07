"""Cost per attempt and budgets (Kiro Crew: per-item credit budgets)."""

import sys
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.mcp_server import BoardTools
from aos.workers.common import parse_cost

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


def test_parse_cost_from_claude_json_output():
    log = 'some noise\n{"type":"result","subtype":"success","total_cost_usd":0.2758,"num_turns":11}\n'
    assert parse_cost(log) == pytest.approx(0.2758)
    assert parse_cost("no json here") is None
    assert parse_cost('{"type":"result"}') is None


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")

    def write(board_cfg):
        (configured / "aos.yaml").write_text(yaml.safe_dump({
            "board": {"default_worker": "fake", **board_cfg},
            "workers": {"fake": {"adapter": "command", "timeout_min": 0.1,
                                 "command": [sys.executable, str(FAKE), "{task_id}", "{role}"]}}}))
    return configured, b, write


def test_costs_are_recorded_per_attempt_and_role(env, monkeypatch):
    repo, b, write = env
    write({"review": True})
    monkeypatch.setenv("FAKE_MODE", "commit_complete")
    monkeypatch.setenv("FAKE_COST", "0.25")
    monkeypatch.setenv("FAKE_REVIEW_COST", "0.05")
    t = b.add_task("checkout", "api", "a", worker="fake")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(t)["status"] == "done"
    assert b.task_cost(t) == pytest.approx(0.30)
    assert {(c["role"], round(c["usd"], 2)) for c in b.costs(t)} == {("worker", 0.25), ("reviewer", 0.05)}
    st = BoardTools(AOS(repo)).board_status("checkout")
    assert st["cost_usd"] == pytest.approx(0.30) and st["tasks"][0]["cost_usd"] == pytest.approx(0.30)


def test_task_budget_stops_new_attempts(env, monkeypatch):
    repo, b, write = env
    write({"review": False, "task_budget_usd": 0.4})
    monkeypatch.setenv("FAKE_MODE", "crash")
    monkeypatch.setenv("FAKE_COST", "0.25")
    t = b.add_task("checkout", "api", "a", worker="fake", max_attempts=5)
    Dispatcher(repo, tick=0.05).run(once=True)   # attempt 1: $0.25, fails
    Dispatcher(repo, tick=0.05).run(once=True)   # attempt 2: $0.50 total, fails ("exited with code 2" twice -> failed)
    b.retry(t)
    Dispatcher(repo, tick=0.05).run(once=True)   # over budget: not started, blocked
    task = b.task(t)
    assert task["status"] == "blocked"
    assert any("budget" in e["body"] and "0.40" in e["body"] for e in b.events("checkout") if e["kind"] == "blocker")


def test_feature_budget_stops_new_tasks(env, monkeypatch):
    repo, b, write = env
    write({"review": False, "feature_budget_usd": 0.2})
    monkeypatch.setenv("FAKE_MODE", "commit_complete")
    monkeypatch.setenv("FAKE_COST", "0.25")
    a = b.add_task("checkout", "api", "a", worker="fake")
    c = b.add_task("checkout", "api", "c", worker="fake", depends_on=[a])
    Dispatcher(repo, tick=0.05).run(once=True)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(a)["status"] == "done" and b.task(c)["status"] == "blocked"
    assert any("feature budget" in e["body"] for e in b.events("checkout") if e["kind"] == "blocker")
