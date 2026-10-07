"""Choosing Claude or Kiro: a global per-user setting, with optional project / feature /
task / run overrides (spec §2)."""

import pytest
import yaml

from aos.board import Board
from aos.cli import main
from aos.config import global_worker, load_user_config, save_user_config
from aos.core import AOS
from aos.mcp_server import BoardTools
from aos.workers.common import resolve_tool


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def aos(configured, data):
    a = AOS(configured)
    for p in ("api", "web"):
        a.register_project(p)
    return a


def set_project_tool(aos, project, profile):
    p = aos.data / "projects.yaml"
    d = yaml.safe_load(p.read_text())
    d["projects"][project]["worker"] = {"profile": profile}
    p.write_text(yaml.safe_dump(d))


def test_precedence(aos):
    assert resolve_tool(aos, "api") == ("claude", "board default")
    save_user_config({**load_user_config(), "worker": "kiro"})
    assert resolve_tool(aos, "api") == ("kiro", "global (aos worker)")
    set_project_tool(aos, "api", "claude")
    assert resolve_tool(aos, "api") == ("claude", "project api")       # an explicit project wins
    assert resolve_tool(aos, "web", feature_worker="claude") == ("claude", "feature")
    assert resolve_tool(aos, "web", requested="claude", feature_worker="kiro") == ("claude", "task")


def test_aos_worker_command(aos, capsys):
    code, out, _ = run(["worker"], capsys)
    assert code == 0 and "claude" in out and "board default" in out
    assert run(["worker", "kiro"], capsys)[0] == 0
    assert global_worker() == "kiro"
    code, out, _ = run(["worker"], capsys)
    assert "kiro" in out and "global" in out
    assert run(["worker", "cursor"], capsys)[0] == 1


def test_init_sets_the_global_tool(configured, capsys):
    assert run(["init", str(configured), "--worker", "kiro"], capsys)[0] == 0
    assert load_user_config()["worker"] == "kiro"


def test_feature_default_is_used_by_new_tasks(aos, capsys):
    assert run(["feature", "new", "checkout", "--title", "Checkout", "--worker", "kiro"], capsys)[0] == 0
    board = Board(aos.data)
    assert board.feature("checkout")["worker"] == "kiro"
    code, out, _ = run(["task", "add", "checkout", "api", "Orders"], capsys)
    assert code == 0 and board.task(1)["worker"] == "kiro"
    planner = BoardTools(AOS(aos.repo))
    planner.feature_create("other", "Other", worker="claude")
    assert planner.task_create("web", "x", feature="other")["worker"] == "claude"


def test_run_override_switches_and_records(aos, monkeypatch):
    from aos.dispatcher import Dispatcher
    board = Board(aos.data)
    board.create_feature("checkout", "Checkout")
    t = board.add_task("checkout", "ghost", "a", worker="claude")
    board.promote()
    d = Dispatcher(aos.repo, worker="kiro", tick=0.05)
    d.tick()  # 'ghost' isn't linked, so the launch blocks; the switch happens first
    assert board.task(t)["worker"] == "kiro"
    assert any(e["kind"] == "status" and "claude -> kiro" in e["body"] for e in board.events("checkout"))


def test_board_status_shows_tool_and_transport(aos):
    board = Board(aos.data)
    board.create_feature("checkout", "Checkout")
    board.add_task("checkout", "api", "a", worker="kiro")
    st = BoardTools(AOS(aos.repo)).board_status("checkout")
    assert st["tasks"][0]["worker"] == "kiro" and "transport" in st["tasks"][0]


def test_skill_mentions_the_global_setting():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "skills/aos-feature/SKILL.md").read_text()
    assert "aos worker" in text


def test_aos_board_shows_tool(aos, capsys):
    board = Board(aos.data)
    board.create_feature("checkout", "Checkout")
    t = board.add_task("checkout", "api", "a", worker="kiro")
    board.set_transport(t, "acp")
    code, out, _ = run(["board"], capsys)
    assert "kiro/acp" in out
