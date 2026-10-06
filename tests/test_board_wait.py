import threading
import time

import pytest

from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.mcp_server import BoardTools


@pytest.fixture
def bt(configured, data):
    aos = AOS(configured)
    for p in ("api", "web"):
        aos.register_project(p)
    b = BoardTools(aos)
    b.wait_poll_s = 0.05
    b.feature_create("checkout", "Checkout")
    return b


def test_finished_returns_results(bt):
    t = bt.task_create("api", "a", feature="checkout")["task"]
    board = bt.board
    board.promote()
    board.claim(t)
    board.complete(t, "endpoint live", author="w")
    r = bt.board_wait("checkout", timeout_sec=2)
    assert r["reason"] == "finished" and r["tasks"][0]["result"] == "endpoint live"


def test_stalled_when_ready_work_but_no_dispatcher(bt):
    bt.task_create("api", "a", feature="checkout")
    bt.board.promote()
    r = bt.board_wait("checkout", timeout_sec=2)
    assert r["reason"] == "stalled" and "board_start" in r["message"]


def test_attention_once_then_waiting_on_human(bt):
    t = bt.task_create("api", "a", feature="checkout")["task"]
    board = bt.board
    board.promote()
    board.claim(t)
    board.block(t, "need API key", author="w")
    r = bt.board_wait("checkout", timeout_sec=2)
    assert r["reason"] == "attention"
    [item] = r["attention"]
    assert item["task"] == t and item["status"] == "blocked" and item["blocker"] == "need API key"
    r2 = bt.board_wait("checkout", cursor=r["cursor"], timeout_sec=2)
    assert r2["reason"] == "waiting_on_human" and r2["pending"][0]["task"] == t


def test_wakes_up_when_a_task_needs_attention(bt):
    t = bt.task_create("api", "a", feature="checkout")["task"]
    board = bt.board
    board.promote()
    board.claim(t)
    d = Dispatcher(bt.aos.repo, tick=0.05)
    with d._lock():  # a dispatcher is "running"
        threading.Timer(0.5, lambda: board.propose(t, "add currency", "need it", author="w")).start()
        start = time.monotonic()
        r = bt.board_wait("checkout", timeout_sec=10)
    assert r["reason"] == "attention" and time.monotonic() - start < 5
    assert r["attention"][0]["proposals"][0]["change"] == "add currency"


def test_timeout_while_work_is_running(bt):
    t = bt.task_create("api", "a", feature="checkout")["task"]
    bt.board.promote()
    bt.board.claim(t)
    with Dispatcher(bt.aos.repo, tick=0.05)._lock():
        r = bt.board_wait("checkout", timeout_sec=0.5)
    assert r["reason"] == "timeout" and r["counts"] == {"running": 1}


def test_stuck_dependency_needs_attention(bt):
    a = bt.task_create("api", "a", feature="checkout")["task"]
    b = bt.task_create("web", "b", feature="checkout", depends_on=[a])["task"]
    bt.board.cancel(a)
    r = bt.board_wait("checkout", timeout_sec=2)
    assert r["reason"] == "attention"
    assert any(i["task"] == b and i["status"] == "stuck" for i in r["attention"])


def test_wait_is_a_planner_tool(bt):
    import asyncio
    from aos.mcp_server import Tools, build_server
    names = {t.name for t in asyncio.run(build_server(Tools(bt.aos), bt).list_tools())}
    assert "board_wait" in names


def test_cursor_round_trips_as_object_or_string(bt):
    import asyncio
    import json
    from aos.mcp_server import Tools, build_server
    t = bt.task_create("api", "a", feature="checkout")["task"]
    board = bt.board
    board.promote()
    board.claim(t)
    board.block(t, "need API key", author="w")
    r = bt.board_wait("checkout", timeout_sec=2)
    assert isinstance(r["cursor"], dict)
    assert bt.board_wait("checkout", cursor=r["cursor"], timeout_sec=2)["reason"] == "waiting_on_human"
    assert bt.board_wait("checkout", cursor=json.dumps(r["cursor"]), timeout_sec=2)["reason"] == "waiting_on_human"
    server = build_server(Tools(bt.aos), bt)
    result = asyncio.run(server.call_tool("board_wait", {"feature": "checkout", "cursor": r["cursor"],
                                                         "timeout_sec": 2}))
    assert "waiting_on_human" in json.dumps(result, default=str)
