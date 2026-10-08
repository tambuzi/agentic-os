"""ACP JSON-RPC client over stdio (aos/acp/rpc.py)."""

import ast
import sys
import time
from pathlib import Path

import pytest

from aos.acp.rpc import AcpConnection, AcpError

FAKE = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_MODE", "echo")
    c = AcpConnection(FAKE, cwd=tmp_path, log_path=tmp_path / "agent.log")
    yield c
    c.close()


def test_request_response_and_notifications(conn):
    init = conn.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
    assert init["agentCapabilities"]["loadSession"] is True
    sid = conn.request("session/new", {"cwd": "/tmp", "mcpServers": []})["sessionId"]
    pending = conn.request_async("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "hello"}]})
    assert pending.wait(10) and pending.result()["stopReason"] == "end_turn"
    kind, method, params = conn.events.get(timeout=5)
    assert (kind, method) == ("notification", "session/update")
    assert params["update"]["content"]["text"] == "hello"
    assert time.monotonic() - conn.last_activity < 5


def test_errors_and_unknown_methods(conn):
    with pytest.raises(AcpError) as e:
        conn.request("no/such/method", {})
    assert e.value.code == -32601


def test_agent_requests_are_queued_and_answered(conn):
    conn.request("test/ask_client", {})
    kind, req_id, method, params = conn.events.get(timeout=5)
    assert (kind, req_id, method) == ("request", 777, "fs/read_text_file")
    conn.respond(req_id, error={"code": -32601, "message": "not implemented"})


def test_malformed_lines_are_ignored(conn):
    assert conn.request("test/garbage", {}) == {"ok": True}


def test_exit_fails_pending_requests(conn):
    pending = conn.request_async("test/exit", {})
    assert pending.wait(10)
    with pytest.raises(AcpError, match="closed"):
        pending.result()
    deadline = time.monotonic() + 5
    while conn.alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not conn.alive()


def test_stderr_goes_to_the_log(conn, tmp_path):
    conn.request("initialize", {"protocolVersion": 1})
    conn.close()
    assert "fake acp agent started" in (tmp_path / "agent.log").read_text()


def test_rpc_module_never_touches_the_board():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "aos/acp/rpc.py").read_text())
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {"sqlite3", "board", "..board", "aos.board"} & imported


def test_protocol_traffic_goes_to_the_log(conn, tmp_path):
    # kiro-cli acp writes nothing to stderr: without the traffic a worker's log is empty
    conn.request("initialize", {"protocolVersion": 1})
    conn.close()
    lines = (tmp_path / "agent.log").read_text().splitlines()
    assert any(l.startswith("> ") and '"initialize"' in l for l in lines)
    assert any(l.startswith("< ") and "protocolVersion" in l for l in lines)
