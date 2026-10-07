"""Steer a running worker: the user's message reaches the worker at its next board_read
(real mid-turn steering arrives with ACP sessions; see ROADMAP.md)."""

import asyncio

import pytest

from aos.board import Board
from aos.cli import main
from aos.core import AOS
from aos.mcp_server import BoardTools, Tools, build_server
from aos.workers.common import WORKER_PROTOCOL


@pytest.fixture
def setup(configured, data):
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "a")
    b.promote()
    b.claim(t)
    return configured, b, t


def test_steer_reaches_the_worker_once(setup):
    repo, b, t = setup
    worker = BoardTools(AOS(repo, slug="api"), task_id=t)
    first = worker.board_read()
    assert first["messages_for_you"] == []
    planner = BoardTools(AOS(repo))
    assert planner.task_steer(t, "use the v2 endpoint, not v1")["ok"]
    later = worker.board_read(since_event=first["events"][-1]["id"])
    assert [m["message"] for m in later["messages_for_you"]] == ["use the v2 endpoint, not v1"]
    again = worker.board_read(since_event=later["events"][-1]["id"])
    assert again["messages_for_you"] == []
    assert "use the v2 endpoint" in str(worker.task_show()["messages_for_you"])


def test_steer_only_unfinished_tasks(setup):
    repo, b, t = setup
    b.mark_seen(t)
    b.complete(t, "done", author="w")
    assert "error" in BoardTools(AOS(repo)).task_steer(t, "too late")


def test_cli_and_planner_tool(setup, capsys):
    repo, b, t = setup
    assert main(["task", "steer", str(t), "skip the migration for now"]) == 0
    assert any(e["kind"] == "steer" for e in b.events("checkout"))
    names = {x.name for x in asyncio.run(build_server(Tools(AOS(repo)), BoardTools(AOS(repo))).list_tools())}
    assert "task_steer" in names


def test_worker_protocol_mentions_steering():
    assert "messages_for_you" in WORKER_PROTOCOL
