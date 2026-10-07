"""Permission requests outside the allow-list become board items the user decides (spec §5.3)."""

import io
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
from aos.mcp_server import BoardTools

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


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    monkeypatch.setenv("FAKE_ACP_MODE", "permission")
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"review": False, "verify": False},
        "workers": {"claude": {"transport": "acp", "acp_command": FAKE_ACP, "timeout_min": 1}}}))
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "a", worker="claude")
    return configured, b, t, tmp_path


def outcome(tmp_path):
    p = tmp_path / "agent.events"
    events = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
    return next((e["data"] for e in events if e["kind"] == "permission_outcome"), None)


def pending(b):
    return b.permissions(status="pending")


def test_ask_then_approve_continues_the_worker(env, capsys):
    repo, b, t, tmp_path = env
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: pending(b), d)
        [p] = pending(b)
        assert p["task"] == t and "npm publish" in p["summary"]
        planner = BoardTools(AOS(repo))
        w = planner.board_wait("checkout", timeout_sec=2)
        assert w["reason"] == "attention" and w["attention"][0]["permissions"][0]["id"] == p["id"]
        main(["board"])
        assert f"permission #{p['id']}" in capsys.readouterr().out
        assert planner.permission_decide(p["id"], approve=True)["status"] == "approved"
        assert wait_for(lambda: outcome(tmp_path) is not None, d)
        assert outcome(tmp_path) == "allow"
        assert wait_for(lambda: b.task(t)["status"] == "done", d)
    finally:
        for r in list(d.running.values()):
            d._terminate(r)


def test_always_adds_the_rule_to_the_project(env, capsys):
    repo, b, t, tmp_path = env
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: pending(b), d)
        assert main(["task", "approve", str(pending(b)[0]["id"]), "--always"]) == 0
        assert wait_for(lambda: outcome(tmp_path) is not None, d) and outcome(tmp_path) == "always"
        projects = yaml.safe_load((b.data / "projects.yaml").read_text())
        assert "shell:npm publish" in projects["projects"]["api"]["worker"]["allowed_tools"]
    finally:
        for r in list(d.running.values()):
            d._terminate(r)


def test_deny(env):
    repo, b, t, tmp_path = env
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: pending(b), d)
        assert main(["task", "deny", str(pending(b)[0]["id"])]) == 0
        assert wait_for(lambda: outcome(tmp_path) is not None, d) and outcome(tmp_path) == "reject"
    finally:
        for r in list(d.running.values()):
            d._terminate(r)


def test_unanswered_permission_expires(env):
    repo, b, t, tmp_path = env
    cfg = yaml.safe_load((repo / "aos.yaml").read_text())
    cfg["board"]["approval_timeout_min"] = 0.01
    (repo / "aos.yaml").write_text(yaml.safe_dump(cfg))
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: outcome(tmp_path) is not None, d)
        assert outcome(tmp_path) == "reject"
        assert b.permissions()[0]["status"] == "expired"
    finally:
        for r in list(d.running.values()):
            d._terminate(r)


def test_kiro_watch_reports_a_pending_permission(env, capsys, monkeypatch):
    repo, b, t, tmp_path = env
    d = Dispatcher(repo, tick=0.05)
    try:
        assert wait_for(lambda: pending(b), d)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cursor": None})))
        capsys.readouterr()
        main(["board", "watch", "--task", str(t)])
        out = json.loads(capsys.readouterr().out)
        assert out["outcome"] == "new-activity" and "permissions" in out["payload"]
    finally:
        for r in list(d.running.values()):
            d._terminate(r)
