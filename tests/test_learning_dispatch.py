"""Learner runs in the dispatcher (spec §3, §5.1, §6)."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.lessons import LessonStore

ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]


def wait_for(cond, d, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d.tick()
        if cond():
            return True
        time.sleep(0.05)
    return False


def agent_events(tmp_path):
    p = tmp_path / "agent.events"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def stop(d):
    for r in list(d.running.values()):
        d._terminate(r)
    d._reap()


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    monkeypatch.setenv("FAKE_ACP_MODE", "complete")
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")

    def write(board_cfg=None, learning=None):
        (configured / "aos.yaml").write_text(yaml.safe_dump({
            "board": {"review": False, "verify": False, **(board_cfg or {})},
            "learning": {"learner_timeout_min": 1, **(learning or {})},
            "workers": {"claude": {"transport": "acp", "acp_command": FAKE_ACP, "timeout_min": 1}}}))
    write()
    return configured, b, write, tmp_path, data


def corrected_task(b, title="a"):
    tid = b.add_task("checkout", "api", title, worker="claude")
    b.steer(tid, "use the v2 endpoint")   # a correction before it even starts
    return tid


def learned(b, tid):
    return [e["body"] for e in b.task_events(tid) if e["kind"] == "learned"]


def roles(tmp_path):
    return [e["data"].get("role") for e in agent_events(tmp_path) if e["kind"] == "session/new"]


def test_corrected_task_gets_one_learner_run(env):
    repo, b, write, tmp_path, data = env
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: learned(b, tid), d)
        for _ in range(20):
            d.tick()
        assert roles(tmp_path) == ["worker", "learner"]
        assert learned(b, tid) == ["1 new lesson"]
        [lesson] = LessonStore(data, "api").list()[0]
        assert lesson.evidence[0]["kind"] == "steer" and lesson.confidence == 0.6
    finally:
        stop(d)


def test_clean_and_cancelled_tasks_get_none(env):
    repo, b, write, tmp_path, data = env
    clean = b.add_task("checkout", "api", "clean", worker="claude")
    gone = corrected_task(b, "gone")
    b.cancel(gone)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: b.task(clean)["status"] == "done", d)
        for _ in range(30):
            d.tick()
            time.sleep(0.02)
        assert roles(tmp_path) == ["worker"] and learned(b, clean) == [] and learned(b, gone) == []
    finally:
        stop(d)


def test_learner_leaves_the_task_alone(env):
    repo, b, write, tmp_path, data = env
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: b.task(tid)["status"] == "done", d)
        before = {k: b.task(tid)[k] for k in ("status", "generation", "attempts", "review_stage", "result")}
        wt = Path(data).parent / "worktrees" / "checkout" / "api"
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True).stdout
        assert wait_for(lambda: learned(b, tid), d)
        assert {k: b.task(tid)[k] for k in before} == before
        assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True).stdout == head
    finally:
        stop(d)


@pytest.mark.parametrize("mode,why", [("crash", "learning failed: exited with code 2"),
                                      ("silent", "learning failed: timed out")])
def test_learner_failure_only_adds_events(env, monkeypatch, mode, why):
    repo, b, write, tmp_path, data = env
    write(learning={"learner_timeout_min": 0.02})
    monkeypatch.setenv("FAKE_ACP_LEARN", mode)
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: learned(b, tid), d)
        assert learned(b, tid)[0].startswith(why)
        assert b.task(tid)["status"] == "done"
        assert LessonStore(data, "api").list()[0] == []
    finally:
        stop(d)


def test_nothing_reusable(env, monkeypatch):
    repo, b, write, tmp_path, data = env
    monkeypatch.setenv("FAKE_ACP_LEARN", "nothing")
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: learned(b, tid), d)
        assert learned(b, tid) == ["nothing reusable"]
    finally:
        stop(d)


def test_learner_cost_role(env, monkeypatch):
    repo, b, write, tmp_path, data = env
    monkeypatch.setenv("FAKE_ACP_COST", "0.01")
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: learned(b, tid) and tid not in d.running, d)
        rows = b._rows("SELECT role FROM costs WHERE task=? ORDER BY id", (tid,))
        assert [r["role"] for r in rows] == ["worker", "learner"]
    finally:
        stop(d)


def test_feature_over_budget_skips_learning(env, monkeypatch):
    repo, b, write, tmp_path, data = env
    monkeypatch.setenv("FAKE_ACP_COST", "5")
    write(board_cfg={"feature_budget_usd": 1})
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: learned(b, tid), d)
        assert learned(b, tid)[0].startswith("learning skipped: feature budget reached")
        assert roles(tmp_path) == ["worker"]
    finally:
        stop(d)


def test_learning_disabled(env):
    repo, b, write, tmp_path, data = env
    write(learning={"enabled": False})
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: b.task(tid)["status"] == "done", d)
        for _ in range(20):
            d.tick()
        assert roles(tmp_path) == ["worker"] and learned(b, tid) == []
        assert d.idle()
    finally:
        stop(d)


def test_learner_session_shape(env):
    repo, b, write, tmp_path, data = env
    tid = corrected_task(b)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: learned(b, tid), d)
        new = [e["data"] for e in agent_events(tmp_path) if e["kind"] == "session/new"][-1]
        assert new["meta"]["claudeCode"]["options"]["model"] == "haiku"
        assert new["mcp"] == ["aos"]                      # no graphskill
        assert "--learn" in new["aos_args"]
    finally:
        stop(d)


def test_once_reaps_its_learners(env):
    repo, b, write, tmp_path, data = env
    tid = corrected_task(b)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert learned(b, tid) == ["1 new lesson"]


def test_a_learner_does_not_hold_the_worktree(env, monkeypatch):
    # review: a running learner kept the next task of the same project from starting
    repo, b, write, tmp_path, data = env
    write(board_cfg={"verify": True, "review": True}, learning={"learner_timeout_min": 5})
    monkeypatch.setenv("FAKE_ACP_LEARN", "silent")
    t1 = corrected_task(b, "first")
    t2 = b.add_task("checkout", "api", "second", worker="claude")
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: t1 in d.running and d.running[t1].kind == "learner", d, timeout=30)
        assert wait_for(lambda: b.task(t2)["status"] == "done", d, timeout=30)   # verified and reviewed meanwhile
        assert d.running.get(t1) is not None and d.running[t1].kind == "learner"
    finally:
        stop(d)
