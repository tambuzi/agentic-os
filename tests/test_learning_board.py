"""Correction signals and learnable tasks (spec §5.1)."""

import pytest

from aos.board import Board
from aos.errors import AosError


@pytest.fixture
def b(data):
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    return board


def start(b, title="a", max_attempts=3):
    tid = b.add_task("checkout", "api", title, max_attempts=max_attempts)
    b.promote()
    b.claim(tid)
    return tid


def finish(b, tid):
    b.complete(tid, "did it", author="w", to="review")
    b.accept(tid, "verified ok")


def kinds(b, tid):
    return [s["kind"] for s in b.correction_signals(tid)]


def test_clean_task_has_no_signals_and_is_not_learnable(b):
    tid = start(b)
    finish(b, tid)
    assert kinds(b, tid) == [] and b.learnable("checkout") == []


def test_review_fail_and_retry(b):
    tid = start(b)
    b.complete(tid, "did it", author="w", to="review")
    b.mark_verified(tid, "tests pass")
    task = b.start_review(tid)
    b.review_verdict(tid, False, "missing migration", generation=task["generation"])
    b.claim(tid)
    finish(b, tid)
    signals = b.correction_signals(tid)
    assert [s["kind"] for s in signals] == ["review_fail", "retry"]
    assert "missing migration" in signals[0]["text"]
    assert [t["id"] for t in b.learnable("checkout")] == [tid]


def test_verify_fail(b):
    tid = start(b)
    b.complete(tid, "did it", author="w", to="review")
    b.reject_review(tid, "verification failed: npm test exited 1")
    b.claim(tid)
    finish(b, tid)
    assert kinds(b, tid) == ["verify_fail", "retry"]


def test_attempt_failed_then_retried(b):
    tid = start(b)
    b.attempt_failed(tid, "stalled: no activity for 10 min")
    b.claim(tid)
    finish(b, tid)
    assert kinds(b, tid) == ["retry"]


def test_failed_without_retry_is_still_learnable_from_its_block(b):
    tid = start(b)
    b.block(tid, "the orders table has no currency column", author="w")
    b.cancel(tid)
    assert b.learnable("checkout") == []          # cancelled: never
    t2 = start(b, "b", max_attempts=1)
    b.block(t2, "API spec unclear", author="w")
    b.retry(t2)
    b.promote()
    b.claim(t2)
    b.attempt_failed(t2, "exited without task_complete")
    assert b.task(t2)["status"] == "failed"
    assert kinds(b, t2) == ["block"]
    assert [t["id"] for t in b.learnable("checkout")] == [t2]


def test_steer_and_denied_permission_but_not_expired(b):
    tid = start(b)
    b.steer(tid, "use the v2 endpoint")
    p1 = b.request_permission(tid, "run `npm publish`")
    b.decide_permission(p1, approve=False)
    p2 = b.request_permission(tid, "run `curl x`")
    b.expire_permission(p2, "no answer in time")
    finish(b, tid)
    assert kinds(b, tid) == ["steer", "permission_denied"]


def test_mark_learned_and_request_learning(b):
    tid = start(b)
    b.steer(tid, "use v2")
    finish(b, tid)
    assert [t["id"] for t in b.learnable()] == [tid]
    b.mark_learned(tid, "1 new lesson")
    assert b.learnable() == []
    b.request_learning(tid)
    assert [t["id"] for t in b.learnable()] == [tid]
    b.mark_learned(tid, "nothing reusable")
    assert b.learnable() == []


def test_request_learning_on_a_clean_task(b):
    tid = start(b)
    finish(b, tid)
    b.request_learning(tid)
    assert [t["id"] for t in b.learnable()] == [tid]


def test_request_learning_refuses_cancelled_and_unfinished(b):
    tid = start(b)
    with pytest.raises(AosError):
        b.request_learning(tid)          # still running
    b.cancel(tid)
    with pytest.raises(AosError):
        b.request_learning(tid)
