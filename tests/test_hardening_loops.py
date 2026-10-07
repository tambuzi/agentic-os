"""Stop repeated failures: the same failure twice in a row ends the task instead of
burning more attempts (Kiro Crew: TaskRunner loop detection)."""

import pytest

from aos.board import Board


@pytest.fixture
def board(data):
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "a", max_attempts=5)
    b.promote()
    return b, t


def fail(b, t, reason):
    b.claim(t)
    b.attempt_failed(t, reason)


def test_same_failure_twice_fails_the_task(board):
    b, t = board
    fail(b, t, "exited with code 1")
    assert b.task(t)["status"] == "ready"
    fail(b, t, "exited  with code 1 ")  # whitespace differences don't make it new
    assert b.task(t)["status"] == "failed"
    assert any("same failure twice" in e["body"] for e in b.events("checkout"))


def test_different_failures_keep_retrying(board):
    b, t = board
    fail(b, t, "exited with code 1")
    fail(b, t, "timed out after 45 min")
    fail(b, t, "exited with code 1")
    assert b.task(t)["status"] == "ready" and b.task(t)["attempts"] == 3


def test_human_retry_starts_fresh(board):
    b, t = board
    fail(b, t, "exited with code 1")
    fail(b, t, "exited with code 1")
    b.retry(t, note="fixed the env")
    fail(b, t, "exited with code 1")
    assert b.task(t)["status"] == "ready"
