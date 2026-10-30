"""Worker messages on the board: messages, replies, answers, limits (spec 2026-10-30 §3-§5)."""

import pytest

from aos.board import Board
from aos.errors import AosError


@pytest.fixture
def b(data):
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    board.create_feature("other", "Other")
    for title, project in (("api work", "api"), ("web work", "web"), ("mobile work", "mobile")):
        board.add_task("checkout", project, title)
    board.add_task("other", "api", "elsewhere")
    board.promote()
    for tid in (1, 2, 3):
        board.claim(tid)
    return board


def test_message_and_broadcast_are_recorded(b):
    m = b.send_message(1, 2, "orders now return total_cents")
    bc = b.send_message(1, "all", "renamed total to total_cents")
    assert m["delivered"] == "queued" and m["auto_reply"] is None
    ev = b.message(m["message"])
    assert (ev["kind"], ev["from"], ev["to"], ev["question"], ev["text"]) == \
        ("message", 1, 2, False, "orders now return total_cents")
    assert b.message(bc["message"])["to"] == "all"
    assert [x["id"] for x in b.messages_for(2)] == [m["message"], bc["message"]]
    assert [x["id"] for x in b.messages_for(3)] == [bc["message"]]
    assert b.messages_for(1) == []                                   # never your own broadcast
    assert [x["id"] for x in b.messages_for(2, since=m["message"])] == [bc["message"]]


@pytest.mark.parametrize("to,text,settings,match", [
    (2, "   ", None, "empty"),
    (2, "x" * 1501, None, "1500"),
    (1, "hi", None, "yourself"),
    (4, "hi", None, "feature"),
    (99, "hi", None, "unknown task"),
    (2, "use api_key = sk-abcdefghijklmnopqrstuv", None, "secret"),
    (2, "hi", {"enabled": False}, "disabled"),
])
def test_refusals(b, to, text, settings, match):
    with pytest.raises(AosError, match=match):
        b.send_message(1, to, text, settings=settings)


def test_cancelled_target_and_duplicates(b):
    b.cancel(3)
    with pytest.raises(AosError, match="cancelled"):
        b.send_message(1, 3, "hello")
    b.send_message(1, 2, "same text")
    with pytest.raises(AosError, match="already sent"):
        b.send_message(1, 2, "same text")


def test_limit_counts_messages_not_replies(b):
    q = b.send_message(2, 1, "which field holds the currency?", question=True)
    for i in range(5):
        b.send_message(1, 2, f"note {i}")
    with pytest.raises(AosError, match="task_comment"):
        b.send_message(1, 2, "note 5")
    assert b.reply(1, q["message"], "currency is in total_currency")["reply"]   # replies still allowed
    b.attempt_failed(1, "crashed")
    b.promote()
    b.claim(1)                                                       # a new attempt: the count restarts
    assert b.send_message(1, 2, "note again")["message"]


def test_reply_only_to_a_question_once(b):
    note = b.send_message(2, 1, "fyi: web uses total")
    with pytest.raises(AosError, match="question"):
        b.reply(1, note["message"], "ok")
    q = b.send_message(2, 1, "which endpoint?", question=True)
    with pytest.raises(AosError, match="addressed"):
        b.reply(3, q["message"], "not mine to answer")
    r = b.reply(1, q["message"], "POST /orders")
    with pytest.raises(AosError, match="already"):
        b.reply(1, q["message"], "again")
    with pytest.raises(AosError, match="question"):
        b.reply(2, r["reply"], "thanks")                              # no replies to replies
    qa = b.send_message(2, "all", "anyone using the old payload?", question=True)
    assert b.reply(3, qa["message"], "mobile does")["reply"]
    reply = b.messages_for(2)[-1]
    assert (reply["kind"], reply["from"], reply["to"], reply["re"]) == ("reply", 3, 2, qa["message"])


def test_question_to_a_finished_task_gets_the_automatic_reply(b):
    b.complete(1, "Added POST /orders returning total_cents\nmore details", author="w")
    q = b.send_message(2, 1, "does /orders return cents?", question=True)
    assert q["delivered"] == "recorded" and q["auto_reply"]
    auto = b.message(q["auto_reply"])
    assert auto["author"] == "aos" and auto["to"] == 2 and auto["re"] == q["message"]
    assert "done" in auto["text"] and "Added POST /orders returning total_cents" in auto["text"]
    assert "escalate=true" in auto["text"] and "more details" not in auto["text"]
    note = b.send_message(2, 1, "thanks, adapting")
    assert note["auto_reply"] is None


def test_question_to_a_task_in_review_gets_the_automatic_reply(b):
    b.complete(1, "Added POST /orders", author="w", to="review")
    q = b.send_message(2, 1, "cents?", question=True)
    assert q["auto_reply"] and "is waiting for review" in b.message(q["auto_reply"])["text"]


def test_escalated_question_is_open_until_answered(b):
    b.complete(1, "Added POST /orders", author="w")
    q = b.send_message(2, 1, "is the currency always EUR?", question=True, escalate=True)
    assert q["auto_reply"] is None                                   # it goes to the planner instead
    [open_q] = b.open_questions("checkout")
    assert open_q["id"] == q["message"] and open_q["escalated"] is True and open_q["from"] == 2
    plain = b.send_message(3, 2, "plain question?", question=True)
    with pytest.raises(AosError, match="escalated"):
        b.answer(plain["message"], "planner can't take this one")
    a = b.answer(q["message"], "yes, EUR only for now")
    assert b.message(a["reply"])["author"] == "planner"
    assert [x["id"] for x in b.open_questions("checkout")] == [plain["message"]]


def test_open_questions_closed_by_a_reply(b):
    q = b.send_message(2, 1, "which endpoint?", question=True)
    assert [x["id"] for x in b.open_questions("checkout")] == [q["message"]]
    b.reply(1, q["message"], "POST /orders")
    assert b.open_questions("checkout") == []


def test_thread(b):
    a = b.send_message(1, 2, "to web")
    c = b.send_message(3, "all", "to everyone")
    d = b.send_message(2, 3, "web to mobile")
    assert [x["id"] for x in b.thread(2)] == [a["message"], c["message"], d["message"]]
    assert [x["id"] for x in b.thread(1)] == [a["message"], c["message"]]


# -- review fixes ------------------------------------------------------------------
def test_unanswered_question_gets_the_automatic_reply_when_the_target_finishes(b):
    # review I4: spec §5 — the target's session ends without answering
    q = b.send_message(2, 3, "which field?", question=True)
    b.complete(3, "Mobile uses total_cents", author="w")
    assert b.open_questions("checkout") == []
    auto = [m for m in b.messages_for(2) if m["kind"] == "reply" and m["re"] == q["message"]]
    assert len(auto) == 1 and auto[0]["from"] == "aos" and "Mobile uses total_cents" in auto[0]["text"]


def test_automatic_reply_is_from_aos_and_does_not_block_a_real_reply(b):
    # review I5
    b.complete(2, "web done", author="w", to="review")
    q = b.send_message(1, 2, "cents?", question=True)
    assert b.message(q["auto_reply"])["from"] == "aos"
    b.reject_review(2, "independent review: missing test")
    b.claim(2)
    assert b.reply(2, q["message"], "yes, cents")["reply"]


def test_a_target_reply_closes_an_escalated_question_and_answers_are_once(b):
    # review M1/M2
    q = b.send_message(2, 1, "is the currency EUR?", escalate=True)
    b.reply(1, q["message"], "yes")
    assert b.open_questions("checkout") == []
    b.complete(3, "done", author="w")
    q2 = b.send_message(2, 3, "and taxes?", escalate=True)
    b.answer(q2["message"], "VAT included")
    with pytest.raises(AosError, match="already"):
        b.answer(q2["message"], "again")
