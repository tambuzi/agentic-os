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
    assert len({t.name for t in asyncio.run(build_server(base).list_tools())}) == 13


@pytest.fixture
def started(configured, data, git_repo, tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    import yaml
    from aos.config import load_user_config, save_user_config
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(root))
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"default_worker": "fake"},
        "workers": {"fake": {"adapter": "command", "timeout_min": 0.1,
                             "command": [sys.executable, str(Path(__file__).parent / "fake_worker.py"), "{task_id}"]}}}))
    projects = {}
    for name in ("api", "web"):
        projects[name] = {"path": str(git_repo(tmp_path / name)), "targets": ["claude"]}
        AOS(configured).register_project(name)
    save_user_config({**load_user_config(), "projects": projects})
    bt = BoardTools(AOS(configured))
    bt.feature_create("checkout", "Checkout")
    return bt


def wait_for(cond, timeout=20.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_board_start_runs_workers_in_background_until_done(started):
    from aos.dispatcher import dispatcher_status
    bt = started
    assert "error" in bt.board_start("checkout")  # no tasks yet
    a = bt.task_create("api", "a", feature="checkout")["task"]
    bt.task_create("web", "b", feature="checkout", depends_on=[a])
    r = bt.board_start("checkout")
    assert r["started"] and r["pid"] > 0 and r["log"].endswith("dispatcher-checkout.log")
    again = bt.board_start("checkout")
    assert again["started"] is False and "already running" in again["message"]
    assert wait_for(lambda: all(t["status"] == "done" for t in bt.board.tasks(feature="checkout")))
    assert wait_for(lambda: not dispatcher_status(bt.board.data)["running"])
    st = bt.board_status("checkout")
    assert st["counts"] == {"done": 2} and st["dispatcher"]["running"] is False


def test_board_start_refuses_when_another_feature_holds_the_dispatcher(started):
    from aos.dispatcher import Dispatcher
    bt = started
    bt.feature_create("other", "Other")
    bt.task_create("api", "x", feature="other")
    bt.task_create("api", "y", feature="checkout")
    with Dispatcher(bt.aos.repo, feature="checkout", tick=0.05)._lock():
        r = bt.board_start("other")
    assert "error" in r and "checkout" in r["error"]


def test_board_status_reports_blockers_and_proposals(started):
    bt = started
    t = bt.task_create("api", "a", feature="checkout")["task"]
    board = bt.board
    board.promote()
    board.claim(t)
    board.propose(t, "add currency", "need it", author=f"task:{t}")
    board.block(t, "missing credentials", author=f"task:{t}")
    st = bt.board_status("checkout")
    assert st["counts"] == {"blocked": 1}
    assert st["blocked"] == [{"task": t, "project": "api", "reason": "missing credentials"}]
    assert st["pending_proposals"][0]["body"] == "add currency"
    assert st["tasks"][0]["title"] == "a"


def test_planner_registers_start_and_status(planner):
    names = {t.name for t in asyncio.run(build_server(Tools(planner.aos), planner).list_tools())}
    assert {"board_start", "board_status"} <= names


def test_planner_acts_on_human_answers(worker):
    w, board, tid = worker
    planner = BoardTools(AOS(w.aos.repo))
    p = w.task_propose_contract("add currency", "multi-currency")["proposal"]
    assert planner.proposal_decide(p, approve=True, reason="ok")["contract_version"] == 2
    p2 = w.task_propose_contract("drop field", "nah")["proposal"]
    assert planner.proposal_decide(p2, approve=False, reason="keep it")["rejected"] == p2
    w.task_block("need API key")
    assert planner.task_unblock(tid, note="key in vault")["status"] == "ready"
    assert board.task(tid)["note"] == "key in vault"
    board.claim(tid)
    board.attempt_failed(tid, "crash")  # second attempt of max 2 -> failed
    assert board.task(tid)["status"] == "failed"
    assert planner.task_retry(tid, note="smaller steps")["status"] == "ready"
    assert planner.task_cancel(tid)["status"] == "cancelled"
    assert "error" in planner.task_unblock(tid)


def test_feature_workflow_tool_writes_into_session_project(planner, configured, tmp_path):
    from aos.config import load_user_config, save_user_config
    proj = tmp_path / "api-checkout"
    proj.mkdir()
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["kiro"]}}})
    planner.feature_create("checkout", "Checkout")
    planner.task_create("api", "Orders", feature="checkout")
    assert "error" in planner.feature_workflow("checkout")  # planner session not in a project
    in_api = BoardTools(AOS(configured, slug="api"))
    r = in_api.feature_workflow("checkout")
    assert r["path"] == str(proj / ".kiro/workflows/aos-checkout.workflow.yaml")
    assert (proj / ".kiro/workflows/aos-checkout.workflow.yaml").exists()
    assert "Workflows" in r["message"]


def test_planner_tool_set_includes_decision_tools(planner):
    names = {t.name for t in asyncio.run(build_server(Tools(planner.aos), planner).list_tools())}
    assert {"task_unblock", "task_retry", "task_cancel", "proposal_decide", "feature_workflow"} <= names
