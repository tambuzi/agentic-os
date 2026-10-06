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


def test_task_activity_idle_and_terminal(task, capsys, monkeypatch):
    board, t = task
    board.claim(t)
    r = poll(["--task", str(t)], {"cursor": None}, capsys, monkeypatch)
    assert r["outcome"] == "new-activity" and json.loads(r["payload"])["status"] == "running"
    r2 = poll(["--task", str(t)], {"cursor": r["cursor"]}, capsys, monkeypatch)
    assert r2["outcome"] == "idle" and r2["cursor"] == r["cursor"]
    board.comment(t, "halfway", author=f"task:{t}")
    r3 = poll(["--task", str(t)], {"cursor": r2["cursor"]}, capsys, monkeypatch)
    assert r3["outcome"] == "new-activity" and "halfway" in r3["payload"]
    board.block(t, "need API key", author=f"task:{t}")
    r4 = poll(["--task", str(t)], {"cursor": r3["cursor"]}, capsys, monkeypatch)
    p = json.loads(r4["payload"])
    assert r4["outcome"] == "terminal-state" and p["status"] == "blocked" and p["blocker"] == "need API key"


def test_task_done_reports_result(task, capsys, monkeypatch):
    board, t = task
    board.claim(t)
    board.complete(t, "endpoint live", author=f"task:{t}")
    r = poll(["--task", str(t)], {"cursor": None}, capsys, monkeypatch)
    assert r["outcome"] == "terminal-state" and json.loads(r["payload"])["result"] == "endpoint live"


def test_errors_still_emit_one_json_object(configured, capsys, monkeypatch):
    r = poll(["--task", "999"], {"cursor": None}, capsys, monkeypatch)
    assert r["outcome"] == "terminal-state" and "unknown task" in json.loads(r["payload"])["error"]
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["board", "watch", "--demo", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] in ("new-activity", "terminal-state")
