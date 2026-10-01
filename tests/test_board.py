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
