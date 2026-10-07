"""Live-session lifecycle: stall, end-of-turn nudge, steering, resume (spec §5.2, §5.4, §5.5, §5.7)."""

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher

ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]


def wait_for(cond, d=None, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if d:
            d.tick()
        if cond():
            return True
        time.sleep(0.05)
    return False


def agent_events(tmp_path):
    p = tmp_path / "agent.events"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")

    def write(tool="claude", board_cfg=None, extra=None):
        (configured / "aos.yaml").write_text(yaml.safe_dump({
            "board": {"review": False, "verify": False, **(board_cfg or {})},
            "workers": {tool: {"transport": "acp", "acp_command": FAKE_ACP, "timeout_min": 1, **(extra or {})}}}))
    return configured, b, write, tmp_path


def stop(d):
    for r in list(d.running.values()):
        d._terminate(r)
    d._reap()


def test_silent_worker_is_stalled(env, monkeypatch):
    repo, b, write, tmp_path = env
    write(board_cfg={"stall_min": 0.01})
    monkeypatch.setenv("FAKE_ACP_MODE", "silent")
    t = b.add_task("checkout", "api", "a", worker="claude", max_attempts=1)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(t)["status"] == "failed"
    assert any("stalled: no activity" in e["body"] for e in b.events("checkout") if e["task"] == t)


def test_waiting_for_a_permission_is_not_a_stall(env, monkeypatch):
    repo, b, write, tmp_path = env
    write(board_cfg={"stall_min": 0.01})
    monkeypatch.setenv("FAKE_ACP_MODE", "permission")
    t = b.add_task("checkout", "api", "a", worker="claude")
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: b.permissions(status="pending"), d)
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            d.tick()
            time.sleep(0.05)
        assert b.task(t)["status"] == "running" and t in d.running
    finally:
        stop(d)


def test_turn_without_verdict_gets_one_nudge(env, monkeypatch):
    repo, b, write, tmp_path = env
    write()
    monkeypatch.setenv("FAKE_ACP_MODE", "no_verdict_once")
    t = b.add_task("checkout", "api", "a", worker="claude")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(t)["status"] == "done" and b.task(t)["attempts"] == 1
    prompts = [e["data"] for e in agent_events(tmp_path) if e["kind"] == "prompt"]
    assert len(prompts) == 2 and "without calling task_complete" in prompts[1]


def test_no_verdict_even_after_the_nudge_fails(env, monkeypatch):
    repo, b, write, tmp_path = env
    write()
    monkeypatch.setenv("FAKE_ACP_MODE", "no_verdict")
    t = b.add_task("checkout", "api", "a", worker="claude", max_attempts=1)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(t)["status"] == "failed"
    assert any("even after a nudge" in e["body"] for e in b.events("checkout") if e["task"] == t)


@pytest.mark.parametrize("tool,kind", [("claude", "steer_delivered"), ("kiro", "steer_queued")])
def test_steer_reaches_the_live_session(env, monkeypatch, tool, kind):
    repo, b, write, tmp_path = env
    write(tool, extra={"agents_dir": str(tmp_path / "agents")})
    monkeypatch.setenv("FAKE_ACP_TOOL", tool)
    monkeypatch.setenv("FAKE_ACP_MODE", "slow")
    monkeypatch.setenv("FAKE_ACP_SLOW", "4")
    t = b.add_task("checkout", "api", "a", worker=tool)
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: any(e["kind"] == "prompt" for e in agent_events(tmp_path)), d)
        assert main(["task", "steer", str(t), "use the v2 endpoint"]) == 0
        assert wait_for(lambda: any(e["kind"] == kind for e in b.events("checkout")), d)
        assert any(e["kind"] == "steer" and e["data"] == "use the v2 endpoint" for e in agent_events(tmp_path))
    finally:
        stop(d)


def test_steer_now_on_kiro_cancels_and_reprompts(env, monkeypatch):
    repo, b, write, tmp_path = env
    write("kiro", extra={"agents_dir": str(tmp_path / "agents")})
    monkeypatch.setenv("FAKE_ACP_TOOL", "kiro")
    monkeypatch.setenv("FAKE_ACP_MODE", "slow")
    monkeypatch.setenv("FAKE_ACP_SLOW", "8")
    t = b.add_task("checkout", "api", "a", worker="kiro")
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: any(e["kind"] == "prompt" for e in agent_events(tmp_path)), d)
        assert main(["task", "steer", str(t), "stop and use v2", "--now"]) == 0
        assert wait_for(lambda: [e for e in agent_events(tmp_path) if e["kind"] == "prompt"][-1]["data"]
                        .find("stop and use v2") >= 0, d)
        assert any(e["kind"] == "cancel" for e in agent_events(tmp_path))
        assert any(e["kind"] == "steer_delivered" for e in b.events("checkout"))
    finally:
        stop(d)


def test_retry_resume_loads_the_session(env, monkeypatch):
    repo, b, write, tmp_path = env
    write()
    monkeypatch.setenv("FAKE_ACP_MODE", "complete")
    t = b.add_task("checkout", "api", "a", worker="claude", max_attempts=3)
    b.promote()
    b.claim(t)
    b.set_process(t, None, "sess-previous")
    b.attempt_failed(t, "timed out")
    b.retry(t, note="carry on", resume=True)
    Dispatcher(repo, tick=0.05).run(once=True)
    loads = [e for e in agent_events(tmp_path) if e["kind"] == "session/load"]
    assert loads and loads[0]["data"]["sessionId"] == "sess-previous"
