import pytest

from aos.board import Board
from aos.errors import AosError


@pytest.fixture
def board(data):
    return Board(data)


def feat(board, slug="checkout"):
    board.create_feature(slug, "Checkout v2", brief="# Goal\nNew checkout.\n", contract="POST /orders")
    return slug


def test_create_feature_writes_files_and_event(board, data):
    feat(board)
    f = board.feature("checkout")
    assert f["status"] == "open" and f["contract_version"] == 1
    assert (data / "features/checkout/brief.md").read_text().startswith("# Goal")
    assert board.contract("checkout") == {"version": 1, "text": "POST /orders\n"}
    assert board.events("checkout")[0]["kind"] == "status"
    with pytest.raises(AosError):
        feat(board)
    with pytest.raises(AosError):
        board.create_feature("Bad Slug", "x")
    with pytest.raises(AosError):
        board.feature("nope")


def test_tasks_and_promotion(board):
    feat(board)
    a = board.add_task("checkout", "shop-api", "Add orders endpoint")
    b = board.add_task("checkout", "web", "Call orders endpoint", depends_on=[a])
    assert board.promote() == [a]
    assert board.task(b)["status"] == "todo" and board.task(b)["depends_on"] == [a]
    with board._tx() as c:
        board._transition(c, a, ("ready",), "done", "test", "", result="endpoint live")
    assert board.promote() == [b]
    assert board.dependency_results(b)[0]["result"] == "endpoint live"
    assert [t["id"] for t in board.tasks(feature="checkout", status="ready")] == [b]


def test_add_task_validation(board):
    feat(board)
    feat(board, "other")
    x = board.add_task("other", "web", "x")
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "y", depends_on=[x])
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "y", depends_on=[999])
    with pytest.raises(AosError):
        board.add_task("nope", "web", "y")
    with pytest.raises(AosError):
        board.add_task("checkout", "Bad Project", "y")
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "  ")
    small = Board(board.data, max_tasks_per_feature=1)
    small.add_task("checkout", "web", "1")
    with pytest.raises(AosError) as e:
        small.add_task("checkout", "web", "2")
    assert "max" in e.value.message
    board.set_feature_status("checkout", "done")
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "z")


def test_stuck_on_failed_dependency(board):
    feat(board)
    a = board.add_task("checkout", "api", "a")
    b = board.add_task("checkout", "web", "b", depends_on=[a])
    board.promote()
    with board._tx() as c:
        board._transition(c, a, ("ready",), "failed", "test", "")
    assert board.promote() == []
    assert board.stuck() == {b: [a]}


def test_events_since_and_limit(board):
    feat(board)
    t = board.add_task("checkout", "web", "x")
    for i in range(5):
        board.comment(t, f"c{i}", author="w")
    evs = board.events("checkout")
    last = evs[-3]["id"]
    assert [e["body"] for e in board.events("checkout", since=last)] == ["c3", "c4"]
    assert [e["body"] for e in board.events("checkout", limit=2)] == ["c3", "c4"]


def test_claim_complete_and_contract_guard(board):
    feat(board)
    t = board.add_task("checkout", "web", "x")
    board.promote()
    assert board.claim(t)["attempts"] == 1
    p = board.propose(t, "POST /orders returns 201", "need status code", author=f"task:{t}")
    board.approve(p)
    with pytest.raises(AosError) as e:
        board.complete(t, "done", author="w")
    assert "v1→v2" in e.value.message
    board.mark_seen(t)
    board.complete(t, "done", author="w")
    assert board.task(t)["status"] == "done" and board.task(t)["result"] == "done"
    assert board.events("checkout")[-1]["kind"] == "result"
    with pytest.raises(AosError):
        board.claim(t)


def test_approve_versions_contract_files(board, data):
    feat(board)
    t = board.add_task("checkout", "web", "x")
    p = board.propose(t, "Add field currency", "multi-currency", author="w")
    assert board.proposals("checkout", "pending")[0]["id"] == p
    assert board.approve(p) == {"proposal": p, "contract_version": 2}
    c = board.contract("checkout")
    assert c["version"] == 2 and "POST /orders" in c["text"] and "Add field currency" in c["text"]
    assert (data / "features/checkout/contract.v1.md").read_text() == "<!-- aos contract v1 -->\nPOST /orders\n"
    with pytest.raises(AosError):
        board.approve(p)
    p2 = board.propose(t, "Drop field", "nah", author="w")
    board.reject(p2, "keep it")
    assert board.proposals("checkout", "rejected")[0]["id"] == p2
    p3 = board.propose(t, "x", "y", author="w")
    assert board.approve(p3, new_contract="FULL REWRITE")["contract_version"] == 3
    assert board.contract("checkout")["text"] == "FULL REWRITE\n"
    kinds = [e["kind"] for e in board.events("checkout") if e["kind"] in ("proposal", "contract", "decision")]
    assert kinds == ["proposal", "contract", "proposal", "decision", "proposal", "contract"]


def test_failed_attempts_retry_block_cancel(board):
    feat(board)
    t = board.add_task("checkout", "web", "x", max_attempts=2)
    board.promote()
    board.claim(t)
    board.set_process(t, 4242, "sess-1")
    assert board.task(t)["pid"] == 4242 and board.task(t)["session_id"] == "sess-1"
    board.attempt_failed(t, "crash")
    assert board.task(t)["status"] == "ready" and board.task(t)["pid"] is None
    board.claim(t)
    board.attempt_failed(t, "crash again")
    assert board.task(t)["status"] == "failed"
    board.retry(t, note="try smaller steps")
    task = board.task(t)
    assert task["status"] == "ready" and task["attempts"] == 0 and task["note"] == "try smaller steps"
    board.claim(t)
    board.block(t, "needs API key", author="w")
    assert board.task(t)["status"] == "blocked"
    assert board.events("checkout")[-1]["kind"] == "blocker"
    board.unblock(t, note="key is in vault")
    assert board.task(t)["status"] == "ready" and board.task(t)["note"] == "key is in vault"
    board.cancel(t)
    assert board.task(t)["status"] == "cancelled"
    with pytest.raises(AosError):
        board.retry(t, resume=True, worker="kiro")
    board.retry(t, resume=True)
    assert board.task(t)["resume"] == 1
    with pytest.raises(AosError):
        board.attempt_failed(t, "not running")


def test_stuck_is_transitive(board):
    feat(board)
    a = board.add_task("checkout", "api", "a")
    b = board.add_task("checkout", "web", "b", depends_on=[a])
    c = board.add_task("checkout", "billing", "c", depends_on=[b])
    board.cancel(a)
    assert board.stuck() == {b: [a], c: [b]}


def test_feature_slug_error_names_feature(board):
    with pytest.raises(AosError) as e:
        board.create_feature("Bad Slug", "x")
    assert "feature slug" in e.value.message
