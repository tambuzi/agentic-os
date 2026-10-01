import asyncio

import pytest

from aos.board import Board
from aos.core import AOS
from aos.mcp_server import PLANNER_TOOLS, WORKER_TOOLS, BoardTools, Tools, build_server


@pytest.fixture
def planner(configured, data):
    aos = AOS(configured)
    for p in ("api", "web"):
        aos.register_project(p)
    return BoardTools(aos)


def test_planner_creates_feature_and_tasks(planner):
    assert planner.feature_create("checkout", "Checkout v2", brief="Goal", contract="POST /orders")["slug"] == "checkout"
    a = planner.task_create("api", "Orders endpoint", spec="do it", feature="checkout")["task"]
    b = planner.task_create("web", "Use it", feature="checkout", depends_on=[a])["task"]
    shown = planner.feature_show("checkout")
    assert [t["id"] for t in shown["tasks"]] == [a, b] and shown["contract"]["version"] == 1
    assert shown["tasks"][0]["worker"] == "claude"
    assert "error" in planner.task_create("ghost", "x", feature="checkout")
    assert "error" in planner.task_create("api", "x")
    assert planner.board_read(feature="checkout")["events"]


@pytest.fixture
def worker(planner):
    planner.feature_create("checkout", "Checkout v2", contract="POST /orders")
    tid = planner.task_create("api", "Orders endpoint", feature="checkout")["task"]
    board = planner.board
    board.promote()
    board.claim(tid)
    return BoardTools(AOS(planner.aos.repo, slug="api"), task_id=tid), board, tid


def test_worker_show_comment_create(worker):
    w, board, tid = worker
    shown = w.task_show()
    assert shown["task"]["id"] == tid and shown["contract"]["text"].startswith("POST /orders")
    assert w.task_comment("halfway")["ok"]
    follow = w.task_create("web", "Consume endpoint", spec="call it")["task"]
    assert board.task(follow)["feature"] == "checkout" and board.task(follow)["created_by"] == f"task:{tid}"
    assert "error" in w.task_create("ghost", "x")


def test_worker_complete_refused_until_seen(worker):
    w, board, tid = worker
    p = w.task_propose_contract("add currency", "multi-currency")["proposal"]
    board.approve(p)
    r = w.task_complete("all done")
    assert "error" in r and "v1→v2" in r["error"]
    read = w.board_read()
    assert read["contract_version"] == 2 and any(e["kind"] == "contract" for e in read["events"])
    assert w.task_complete("all done")["ok"]
    assert board.task(tid)["status"] == "done"


def test_worker_block(worker):
    w, board, tid = worker
    assert w.task_block("missing credentials")["ok"]
    assert board.task(tid)["status"] == "blocked"


def test_tool_registration_by_mode(planner, worker):
    w, _, _ = worker
    base = Tools(planner.aos)
    names = {t.name for t in asyncio.run(build_server(base, planner).list_tools())}
    assert PLANNER_TOOLS <= names and not (WORKER_TOOLS - PLANNER_TOOLS) & names
    names = {t.name for t in asyncio.run(build_server(base, w).list_tools())}
    assert WORKER_TOOLS <= names and "feature_create" not in names
    assert len({t.name for t in asyncio.run(build_server(base).list_tools())}) == 11
