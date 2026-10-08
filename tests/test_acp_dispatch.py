"""The dispatcher runs workers and reviewers over ACP (spec §3, §5.1, §5.6)."""

import json
import stat
import sys
import time
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher

ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]


def wait_for(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")

    def write(workers, board_cfg=None):
        (configured / "aos.yaml").write_text(yaml.safe_dump({
            "board": {"review": True, **(board_cfg or {})}, "workers": workers}))
    return configured, b, write, tmp_path


def agent_events(tmp_path):
    p = tmp_path / "agent.events"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_claude_worker_and_reviewer_over_acp(env, monkeypatch):
    repo, b, write, tmp_path = env
    write({"claude": {"transport": "acp", "acp_command": FAKE_ACP, "timeout_min": 1}})
    monkeypatch.setenv("FAKE_ACP_MODE", "complete")
    monkeypatch.setenv("FAKE_ACP_COST", "0.10")
    t = b.add_task("checkout", "api", "a", worker="claude")
    Dispatcher(repo, tick=0.05).run(once=True)
    task = b.task(t)
    assert task["status"] == "done" and task["transport"] == "acp"
    kinds = [e["kind"] for e in b.events("checkout") if e["task"] == t]
    assert "verified" in kinds and "reviewed" in kinds
    assert [(c["role"], c["unit"]) for c in b.costs(t)] == [("worker", "USD"), ("reviewer", "USD")]
    assert b.task_cost(t) == pytest.approx(0.20)
    news = [e for e in agent_events(tmp_path) if e["kind"] == "session/new"]
    assert news[0]["data"]["meta"]["systemPrompt"]["append"].startswith("# agenticOS context")


def test_kiro_over_acp_is_hermetic_and_costs_credits(env, monkeypatch):
    repo, b, write, tmp_path = env
    agents = tmp_path / "kiro-agents"
    write({"kiro": {"transport": "acp", "acp_command": FAKE_ACP, "agents_dir": str(agents), "timeout_min": 1}})
    monkeypatch.setenv("FAKE_ACP_TOOL", "kiro")
    monkeypatch.setenv("FAKE_KIRO_AGENTS", str(agents))
    monkeypatch.setenv("FAKE_ACP_MODE", "complete")
    monkeypatch.setenv("FAKE_ACP_COST", "0.05")
    t = b.add_task("checkout", "api", "a", worker="kiro")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert b.task(t)["status"] == "done"
    assert any(e["kind"] == "set_mode" and e["data"] == f"aos-checkout-t{t}" for e in agent_events(tmp_path))
    assert {c["unit"] for c in b.costs(t)} == {"credit"}
    assert not list(agents.glob("*.json"))  # per-run agent files cleaned up


def test_missing_adapter_falls_back_to_the_cli(env, tmp_path):
    repo, b, write, _ = env
    fake_cli = tmp_path / "fake-claude"
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    write({"claude": {"transport": "acp", "acp_adapter": str(tmp_path / "missing.js"), "bin": str(fake_cli),
                      "timeout_min": 1}}, {"review": False})
    t = b.add_task("checkout", "api", "a", worker="claude", max_attempts=1)
    Dispatcher(repo, tick=0.05).run(once=True)
    task = b.task(t)
    assert task["transport"] == "cli" and task["status"] == "failed"  # the fake CLI does nothing
    assert any("ACP unavailable" in e["body"] for e in b.events("checkout") if e["task"] == t)


def test_cancel_closes_the_session_and_process(env, monkeypatch):
    repo, b, write, tmp_path = env
    write({"claude": {"transport": "acp", "acp_command": FAKE_ACP, "timeout_min": 1}})
    monkeypatch.setenv("FAKE_ACP_MODE", "silent")
    t = b.add_task("checkout", "api", "a", worker="claude")
    d = Dispatcher(repo, tick=0.05)
    d.tick()
    proc = d.running[t].proc
    assert wait_for(lambda: any(e["kind"] == "prompt" for e in agent_events(tmp_path)))
    b.cancel(t)
    d.tick()
    assert proc.poll() is not None and t not in d.running
    assert b.task(t)["status"] == "cancelled"
