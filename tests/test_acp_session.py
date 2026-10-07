"""Per-tool ACP facts (aos/acp/tools.py) and the session wrapper (aos/acp/session.py)."""

import json
import sys
import time
from pathlib import Path

import pytest

from aos.acp import tools
from aos.acp.rpc import AcpConnection
from aos.acp.session import AcpWorkerSession
from aos.acp.tools import AdapterMissing

FAKE = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]
SERVERS = {"aos": {"command": "/bin/aos", "args": ["serve", "--project", "/p", "--task", "7", "--attempt", "2"]},
           "graphskill": {"command": "gs", "args": ["serve"], "env": {"X": "1"}}}


def seen(log):
    return [json.loads(l) for l in Path(log).read_text().splitlines()] if Path(log).exists() else []


def open_session(tmp_path, monkeypatch, tool, mode="echo", cost=None):
    log = tmp_path / f"{tool}.events"
    monkeypatch.setenv("FAKE_ACP_TOOL", tool)
    monkeypatch.setenv("FAKE_ACP_MODE", mode)
    monkeypatch.setenv("FAKE_ACP_LOG", str(log))
    if cost:
        monkeypatch.setenv("FAKE_ACP_COST", cost)
    conn = AcpConnection(FAKE, cwd=tmp_path)
    return AcpWorkerSession(conn, tool), log


def test_launch_commands(tmp_path, monkeypatch):
    monkeypatch.setattr("aos.acp.tools.shutil.which", lambda n: f"/usr/bin/{n}")
    entry = tmp_path / "index.js"
    entry.write_text("")
    argv, env = tools.launch("claude", {"acp_adapter": str(entry)}, tmp_path)
    assert argv == ["/usr/bin/node", str(entry)] and env["CLAUDE_CODE_EXECUTABLE"] == "/usr/bin/claude"
    argv, _ = tools.launch("kiro", {}, tmp_path)
    assert argv == ["/usr/bin/kiro-cli", "acp"]
    assert tools.launch("claude", {"acp_command": ["x", "y"]}, tmp_path)[0] == ["x", "y"]
    with pytest.raises(AdapterMissing, match="aos acp install claude"):
        tools.launch("claude", {"acp_adapter": str(tmp_path / "missing.js")}, tmp_path)
    monkeypatch.setattr("aos.acp.tools.shutil.which", lambda n: None)
    with pytest.raises(AdapterMissing):
        tools.launch("kiro", {}, tmp_path)
    with pytest.raises(AdapterMissing):
        tools.launch("fake", {}, tmp_path)


def test_option_mapping():
    options = [{"optionId": "a1", "kind": "allow_once"}, {"optionId": "a2", "kind": "allow_always"},
               {"optionId": "r1", "kind": "reject_once"}]
    assert tools.option_for("allow", options) == "a1"
    assert tools.option_for("always", options) == "a2"
    assert tools.option_for("reject", options) == "r1"


def test_claude_session_context_mcp_and_cost(tmp_path, monkeypatch):
    s, log = open_session(tmp_path, monkeypatch, "claude", cost="0.12")
    try:
        sid = s.start(tmp_path, SERVERS, "CONTEXT TEXT")
        new = next(e for e in seen(log) if e["kind"] == "session/new")["data"]
        assert new["meta"] == {"systemPrompt": {"append": "CONTEXT TEXT"}}
        assert set(new["mcp"]) == {"aos", "graphskill"} and new["cwd"] == str(tmp_path)
        turn = s.prompt("do it")
        assert turn.wait(10) and turn.result()["stopReason"] == "end_turn"
        time.sleep(0.2)
        s.drain()
        assert s.cost == pytest.approx(0.12) and s.cost_unit == "USD"
        assert sid
    finally:
        s.close()


def test_kiro_session_selects_the_hermetic_agent(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "aos-checkout-t7.json").write_text("{}")
    monkeypatch.setenv("FAKE_KIRO_AGENTS", str(agents))
    s, log = open_session(tmp_path, monkeypatch, "kiro", cost="0.05")
    try:
        s.start(tmp_path, SERVERS, "CONTEXT TEXT", kiro_agent="aos-checkout-t7")
        assert s.hermetic and any(e["kind"] == "set_mode" and e["data"] == "aos-checkout-t7" for e in seen(log))
        turn = s.prompt("do it")
        assert turn.wait(10)
        assert s.wait_turn_end(5)          # trailing _kiro.dev/metadata
        assert s.cost == pytest.approx(0.05) and s.cost_unit == "credit"
        prompt = next(e for e in seen(log) if e["kind"] == "prompt")["data"]
        assert "CONTEXT TEXT" not in prompt  # the agent file carries it
    finally:
        s.close()


def test_kiro_without_the_agent_falls_back_to_context_in_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KIRO_AGENTS", str(tmp_path / "none"))
    s, log = open_session(tmp_path, monkeypatch, "kiro")
    try:
        s.start(tmp_path, SERVERS, "CONTEXT TEXT", kiro_agent="aos-checkout-t7")
        assert s.hermetic is False
        s.prompt("do it").wait(10)
        assert next(e for e in seen(log) if e["kind"] == "prompt")["data"].startswith("CONTEXT TEXT")
    finally:
        s.close()


def test_resume_uses_session_load(tmp_path, monkeypatch):
    s, log = open_session(tmp_path, monkeypatch, "claude")
    try:
        assert s.start(tmp_path, SERVERS, "ctx", resume_id="old-session") == "old-session"
        assert s.resumed and any(e["kind"] == "session/load" for e in seen(log))
    finally:
        s.close()


def test_steering_per_tool(tmp_path, monkeypatch):
    for tool, expected in (("claude", "injected"), ("kiro", "queued")):
        s, log = open_session(tmp_path, monkeypatch, tool)
        try:
            s.start(tmp_path, SERVERS, "ctx")
            assert s.steer("use the v2 endpoint") == expected
            assert any(e["kind"] == "steer" and e["data"] == "use the v2 endpoint" for e in seen(log))
        finally:
            s.close()
