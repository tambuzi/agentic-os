"""Attempt tokens: a worker's MCP server is bound to the generation it was launched for.
A worker that outlived its attempt must not complete, block, comment on, or propose for
the task's next attempt (Crew: claim/lease/generation)."""

import pytest

from aos.board import Board
from aos.core import AOS
from aos.errors import AosError
from aos.mcp_server import BoardTools
from aos.workers.common import mcp_servers


@pytest.fixture
def board(configured, data):
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "a", max_attempts=5)
    b.promote()
    return b, t


def test_generation_only_grows(board):
    b, t = board
    assert b.claim(t)["generation"] == 1
    b.attempt_failed(t, "crash")
    assert b.claim(t)["generation"] == 2
    b.attempt_failed(t, "crash")
    b.cancel(t)
    b.retry(t)  # resets attempts, never the generation
    task = b.claim(t)
    assert task["attempts"] == 1 and task["generation"] == 3


def test_stale_worker_is_refused(board, configured):
    b, t = board
    b.claim(t)                      # generation 1: the old worker
    old = BoardTools(AOS(configured, slug="api"), task_id=t, attempt=1)
    b.attempt_failed(t, "timed out")
    b.claim(t)                      # generation 2: the new worker
    new = BoardTools(AOS(configured, slug="api"), task_id=t, attempt=2)
    for call in (lambda: old.task_complete("done"), lambda: old.task_block("x"),
                 lambda: old.task_comment("hi"), lambda: old.task_propose_contract("c", "r"),
                 lambda: old.task_create("api", "follow-up")):
        r = call()
        assert "error" in r and "attempt 1" in r["error"] and "stale" in r["error"], r
    assert b.task(t)["status"] == "running"
    assert new.task_complete("done by the current attempt")["ok"]
    assert b.task(t)["status"] == "review"  # a worker's complete is a claim, verified next


def test_board_level_check_is_transactional(board):
    b, t = board
    b.claim(t)
    with pytest.raises(AosError, match="stale"):
        b.complete(t, "x", author="w", generation=7)
    b.complete(t, "x", author="w", generation=1)


def test_reads_are_allowed_for_stale_workers(board, configured):
    b, t = board
    b.claim(t)
    old = BoardTools(AOS(configured, slug="api"), task_id=t, attempt=1)
    b.attempt_failed(t, "x")
    b.claim(t)
    assert "error" not in old.task_show() and "error" not in old.board_read()


def test_worker_mcp_server_gets_the_generation(tmp_path):
    s = mcp_servers(tmp_path, 7, "/bin/aos", generation=3)
    assert s["aos"]["args"] == ["serve", "--project", str(tmp_path), "--task", "7", "--attempt", "3"]
