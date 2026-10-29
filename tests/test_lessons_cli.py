"""User controls for lessons: CLI, planner MCP tools, board_status, doctor (spec §3)."""

import pytest

from aos import inbox
from aos.board import Board
from aos.cli import main
from aos.core import AOS
from aos.lessons import LessonStore
from aos.mcp_server import PLANNER_TOOLS, BoardTools


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def ev(feature="checkout"):
    return [{"feature": feature, "task": 1, "events": [1], "kind": "review_fail", "at": "2026-10-29"}]


@pytest.fixture
def env(configured, data):
    aos = AOS(configured)
    for p in ("api", "web"):
        aos.register_project(p)
    api = LessonStore(data, "api")
    a = api.new("when adding a table", "Add a migration with it.", "database", ev())
    b = api.new("when renaming an endpoint", "Keep the old route as an alias.", "api", ev("other"))
    return aos, api, a, b


def test_list_show_confirm_reject(env, capsys):
    aos, api, a, b = env
    code, out, _ = run(["lessons"], capsys)
    assert code == 0 and f"api/{a.id}" in out and "unreviewed" in out and "when adding a table" in out
    code, out, _ = run(["lessons", "show", f"api/{a.id}"], capsys)
    assert code == 0 and "Add a migration with it." in out and "review_fail" in out
    assert run(["lessons", "confirm", f"api/{a.id}"], capsys)[0] == 0
    assert api.get(a.id).status == "confirmed" and api.get(a.id).confidence == 0.8
    assert run(["lessons", "reject", f"api/{b.id}"], capsys)[0] == 0
    code, out, _ = run(["lessons", "api"], capsys)
    assert f"api/{b.id}" not in out                      # rejected: hidden by default
    assert f"api/{b.id}" in run(["lessons", "api", "--all"], capsys)[1]


def test_confirm_that_completes_a_group_proposes_promotion(env, capsys):
    aos, api, a, b = env
    web = LessonStore(aos.data, "web")
    w = web.decide(web.new("when adding a table", "Migrate it.", "database", ev()).id, True)
    c = api.new("when adding a db table", "Add a migration for it.", "database", ev(),
                same_as=[f"web/{w.id}"], general_rule="Ship a migration with every new table.")
    code, out, _ = run(["lessons", "confirm", f"api/{c.id}"], capsys)
    assert code == 0 and "promotion proposed" in out and "aos inbox show" in out


def test_broken_file_is_reported(env, capsys):
    aos, api, a, b = env
    (api.dir / "broken-0000.md").write_text("---\n: : :\n---\n")
    code, out, err = run(["lessons"], capsys)
    assert code == 0 and "broken-0000.md" in (out + err) and f"api/{a.id}" in out
    code, out, _ = run(["doctor"], capsys)
    assert "lessons: 2 in 1 project" in out and "broken-0000.md" in out


def test_learn_request(env, capsys):
    aos, api, a, b = env
    board = Board(aos.data)
    board.create_feature("checkout", "Checkout")
    tid = board.add_task("checkout", "api", "t")
    board.promote()
    board.claim(tid)
    board.complete(tid, "done", author="w")
    assert run(["lessons", "learn", str(tid)], capsys)[0] == 0
    assert [t["id"] for t in board.learnable()] == [tid]


def test_planner_tools(env):
    aos, api, a, b = env
    assert {"lessons_list", "lesson_decide"} <= PLANNER_TOOLS
    bt = BoardTools(aos)
    refs = [l["ref"] for l in bt.lessons_list()["lessons"]]
    assert sorted(refs) == sorted([f"api/{a.id}", f"api/{b.id}"])
    assert [l["ref"] for l in bt.lessons_list(feature="checkout")["lessons"]] == [f"api/{a.id}"]
    assert bt.lessons_list(project="web")["lessons"] == []
    r = bt.lesson_decide(f"api/{a.id}", confirm=True)
    assert r["status"] == "confirmed" and r["promotions"] == []
    assert [l["ref"] for l in bt.lessons_list(status="confirmed")["lessons"]] == [f"api/{a.id}"]
    assert "error" in bt.lesson_decide("api/nope-0000", confirm=True)


def test_board_status_reports_lessons_and_promotions(env):
    aos, api, a, b = env
    board = Board(aos.data)
    board.create_feature("checkout", "Checkout")
    web = LessonStore(aos.data, "web")
    w = web.decide(web.new("when adding a table", "Migrate it.", "database", ev()).id, True)
    c = api.new("when adding a db table", "Add a migration for it.", "database", ev(),
                same_as=[f"web/{w.id}"], general_rule="Ship a migration with every new table.")
    BoardTools(aos).lesson_decide(f"api/{c.id}", confirm=True)
    status = BoardTools(aos).board_status("checkout")
    refs = {l["ref"]: l for l in status["lessons"]}
    assert f"api/{a.id}" in refs and f"api/{b.id}" not in refs and refs[f"api/{a.id}"]["new"] is True
    [promo] = status["pending_promotions"]
    assert promo["rule"] == "Ship a migration with every new table." and f"api/{c.id}" in promo["lessons"]
    assert inbox.load(aos.data, promo["proposal"])["args"]["scope"] == "org"
