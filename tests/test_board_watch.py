"""`aos board watch`: the Kiro workflow `command` watch-handler contract.
stdin: {"cursor", "config", "workspacePath", ...}; stdout: exactly one JSON object
{"outcome": "idle|new-activity|terminal-state", "cursor": ..., "payload": "<json string>"}."""

import io
import json

import pytest

from aos.board import Board
from aos.cli import main


def poll(argv, stdin, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin)))
    assert main(["board", "watch", *argv]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, out
    return json.loads(out[0])


def test_demo_counts_polls_then_terminates(configured, capsys, monkeypatch):
    r = poll(["--demo", "2"], {"cursor": None, "config": {}, "workspacePath": "/w"}, capsys, monkeypatch)
    assert r["outcome"] == "new-activity" and r["cursor"] == {"n": 1}
    r = poll(["--demo", "2"], {"cursor": r["cursor"]}, capsys, monkeypatch)
    assert r["outcome"] == "terminal-state" and json.loads(r["payload"])["polls"] == 2


@pytest.fixture
def task(configured, data):
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    t = board.add_task("checkout", "api", "a")
    board.promote()
    return board, t


def test_progress_alone_stays_idle(task, capsys, monkeypatch):
    """A Kiro watch node completes on its first new-activity, so plain progress must be idle."""
    board, t = task
    board.claim(t)
    r = poll(["--task", str(t)], {"cursor": None}, capsys, monkeypatch)
    assert r["outcome"] == "idle"
    board.comment(t, "halfway", author=f"task:{t}")
    assert poll(["--task", str(t)], {"cursor": r["cursor"]}, capsys, monkeypatch)["outcome"] == "idle"


def test_blocker_needs_attention_once(task, capsys, monkeypatch):
    board, t = task
    board.claim(t)
    board.block(t, "need API key", author=f"task:{t}")
    r = poll(["--task", str(t)], {"cursor": None}, capsys, monkeypatch)
    p = json.loads(r["payload"])
    assert r["outcome"] == "new-activity" and p["status"] == "blocked" and p["blocker"] == "need API key"
    assert poll(["--task", str(t)], {"cursor": r["cursor"]}, capsys, monkeypatch)["outcome"] == "idle"
    board.unblock(t, "key in vault")
    board.claim(t)
    board.block(t, "now need a DB", author=f"task:{t}")
    r2 = poll(["--task", str(t)], {"cursor": r["cursor"]}, capsys, monkeypatch)
    assert r2["outcome"] == "new-activity" and json.loads(r2["payload"])["blocker"] == "now need a DB"


def test_pending_proposal_and_failure_need_attention(task, capsys, monkeypatch):
    board, t = task
    board.claim(t)
    pid = board.propose(t, "add currency", "multi-currency", author=f"task:{t}")
    r = poll(["--task", str(t)], {"cursor": None}, capsys, monkeypatch)
    p = json.loads(r["payload"])
    assert r["outcome"] == "new-activity" and p["proposals"][0]["id"] == pid
    board.reject(pid, "no")
    board.attempt_failed(t, "crash")
    board.claim(t)
    board.attempt_failed(t, "crash again")
    r2 = poll(["--task", str(t)], {"cursor": r["cursor"]}, capsys, monkeypatch)
    assert r2["outcome"] == "new-activity" and json.loads(r2["payload"])["status"] == "failed"


def test_done_and_cancelled_are_terminal(task, capsys, monkeypatch):
    board, t = task
    board.claim(t)
    board.complete(t, "endpoint live", author=f"task:{t}")
    r = poll(["--task", str(t)], {"cursor": None}, capsys, monkeypatch)
    assert r["outcome"] == "terminal-state" and json.loads(r["payload"])["result"] == "endpoint live"
    u = board.add_task("checkout", "web", "u")
    board.cancel(u)
    assert poll(["--task", str(u)], {"cursor": None}, capsys, monkeypatch)["outcome"] == "terminal-state"


def test_errors_still_emit_one_json_object(configured, capsys, monkeypatch):
    r = poll(["--task", "999"], {"cursor": None}, capsys, monkeypatch)
    assert r["outcome"] == "terminal-state" and "unknown task" in json.loads(r["payload"])["error"]
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["board", "watch", "--demo", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] in ("new-activity", "terminal-state")
