"""Worker message tools, the planner's answer, board_wait attention and the CLI (plan Task 2)."""

import asyncio

import pytest

from aos.board import Board
from aos.cli import main
from aos.core import AOS
from aos.mcp_server import LEARNER_TOOLS, PLANNER_TOOLS, BoardTools, LearnerTools, Tools, build_learner_server, build_server

MESSAGE_TOOLS = {"task_message", "task_broadcast", "task_reply"}


@pytest.fixture
def env(configured, data):
    aos = AOS(configured)
    for p in ("api", "web"):
        aos.register_project(p)
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    b.add_task("checkout", "api", "api work")
    b.add_task("checkout", "web", "web work")
    b.promote()
    t1, t2 = b.claim(1), b.claim(2)
    w1 = BoardTools(aos, task_id=1, attempt=t1["generation"])
    w2 = BoardTools(aos, task_id=2, attempt=t2["generation"])
    return aos, b, w1, w2


def test_worker_tools_send_and_reply(env):
    aos, b, w1, w2 = env
    assert w1.task_broadcast("renamed total to total_cents")["message"]
    q = w2.task_message(1, "is total_cents an int?", question=True)
    assert q["delivered"] == "queued"
    r = w1.task_reply(q["message"], "yes, integer cents")
    assert b.message(r["reply"])["to"] == 2
    assert "error" in w1.task_message(1, "to myself")


def test_stale_worker_cannot_send(env):
    aos, b, w1, w2 = env
    stale = BoardTools(aos, task_id=1, attempt=999)
    assert "error" in stale.task_message(2, "hi")
    assert "error" in stale.task_broadcast("hi")


def test_reviewer_and_learner_have_no_message_tools(env):
    aos, b, w1, w2 = env
    reviewer = BoardTools(aos, task_id=1, attempt=b.task(1)["generation"], review=True)
    names = {t.name for t in asyncio.run(build_server(Tools(aos), reviewer).list_tools())}
    assert not names & MESSAGE_TOOLS
    worker_names = {t.name for t in asyncio.run(build_server(Tools(aos), w1).list_tools())}
    assert MESSAGE_TOOLS <= worker_names
    assert not LEARNER_TOOLS & MESSAGE_TOOLS


def test_board_read_and_task_show_include_messages(env):
    aos, b, w1, w2 = env
    m = w1.task_message(2, "heads-up: total_cents")
    assert [x["id"] for x in w2.board_read()["messages"]] == [m["message"]]
    assert w2.board_read(since_event=m["message"])["messages"] == []
    assert [x["id"] for x in w2.task_show()["messages"]] == [m["message"]]


def test_message_answer_and_board_status(env):
    aos, b, w1, w2 = env
    planner = BoardTools(aos)
    assert "message_answer" in PLANNER_TOOLS
    q = w2.task_message(1, "is the currency always EUR?", escalate=True)
    status = planner.board_status("checkout")
    assert status["messages"] == {"2": 1} and [x["id"] for x in status["open_questions"]] == [q["message"]]
    assert planner.message_answer(q["message"], "EUR only")["reply"]
    assert planner.board_status("checkout")["open_questions"] == []


def test_board_wait_wakes_on_an_escalated_question(env):
    aos, b, w1, w2 = env
    planner = BoardTools(aos)
    q = w2.task_message(1, "which tax rules apply?", escalate=True)
    w = planner.board_wait("checkout", timeout_sec=2)
    assert w["reason"] == "attention"
    [item] = [a for a in w["attention"] if a.get("questions")]
    assert item["task"] == 2 and item["questions"][0]["id"] == q["message"]
    b.complete(2, "done", author="w")                               # the asker finished: still reported
    w = planner.board_wait("checkout", timeout_sec=2)
    assert any(a.get("questions") for a in w["attention"])


def test_cli_board_lists_open_questions_and_task_show_messages(env, capsys):
    aos, b, w1, w2 = env
    q = w2.task_message(1, "which endpoint?", question=True)
    w2.task_message(1, "is the currency always EUR?", escalate=True)
    assert main(["board"]) == 0
    out = capsys.readouterr().out
    assert f"question #{q['message']} from #2 to #1: which endpoint?" in out and "escalated" in out
    assert main(["task", "show", "2", "--messages"]) == 0
    out = capsys.readouterr().out
    assert "which endpoint?" in out and "#2 -> #1" in out
