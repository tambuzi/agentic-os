"""Operation types: a task is a feature, a change, a fix or a refactor, and its worker and
reviewer get the first move and the check that fit it (None = no extra text anywhere)."""

from pathlib import Path

import pytest

from aos.board import OPERATIONS, Board
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.errors import AosError
from aos.mcp_server import BoardTools
from aos.workers.common import build_review_context, build_worker_context

ROOT = Path(__file__).resolve().parents[1]

FIRST_MOVE = {
    "fix": "failing test first",
    "change": "update its tests to match the new behaviour",
    "refactor": "Behaviour must not change",
    "feature": "new tests that exercise it",
}
CHECK = {
    "fix": "regression test that exercises the bug",
    "refactor": "tests deleted, skipped or weakened",
    "change": "the tests cover the changed behaviour",
    "feature": "the tests cover the new behaviour",
}


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def env(configured, data, git_repo, tmp_path):
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    aos = AOS(configured, slug="api")
    aos.register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    return aos, b, proj


# -- board ---------------------------------------------------------------------------
def test_operations_are_the_four_types():
    assert OPERATIONS == ("feature", "change", "fix", "refactor")


def test_add_task_stores_a_valid_operation(env):
    _, b, _ = env
    assert b.task(b.add_task("checkout", "api", "a", operation="fix"))["operation"] == "fix"


def test_add_task_operation_defaults_to_none(env):
    _, b, _ = env
    assert b.task(b.add_task("checkout", "api", "a"))["operation"] is None


def test_add_task_rejects_an_unknown_operation(env):
    _, b, _ = env
    with pytest.raises(AosError, match="feature, change, fix, refactor"):
        b.add_task("checkout", "api", "a", operation="rewrite")


# -- CLI -----------------------------------------------------------------------------
def test_cli_op_flag_and_task_show(env, capsys):
    _, b, _ = env
    assert run(["task", "add", "checkout", "api", "broken total", "--op", "fix"], capsys)[0] == 0
    assert b.task(1)["operation"] == "fix"
    code, out, _ = run(["task", "show", "1"], capsys)
    assert code == 0 and "operation: fix" in out
    with pytest.raises(SystemExit):
        main(["task", "add", "checkout", "api", "bad", "--op", "rewrite"])
    capsys.readouterr()


def test_aos_board_shows_the_operation_tag(env, capsys):
    _, b, _ = env
    b.add_task("checkout", "api", "broken total", operation="fix")
    b.add_task("checkout", "api", "plain task")
    code, out, _ = run(["board"], capsys)
    assert code == 0 and "[fix] broken total" in out
    assert "] plain task" not in out


# -- MCP -----------------------------------------------------------------------------
def test_mcp_task_create_planner_takes_operation(env):
    aos, b, _ = env
    bt = BoardTools(aos)
    r = bt.task_create("api", "Split orders module", feature="checkout", operation="refactor")
    assert b.task(r["task"])["operation"] == "refactor"
    bad = bt.task_create("api", "x", feature="checkout", operation="rewrite")
    assert "error" in bad and "refactor" in bad["error"]


def test_mcp_task_create_worker_takes_operation(env):
    aos, b, _ = env
    tid = b.add_task("checkout", "api", "a")
    b.promote()
    b.claim(tid)
    bt = BoardTools(aos, task_id=tid)
    r = bt.task_create("api", "Fix rounding", operation="fix")
    assert b.task(r["task"])["operation"] == "fix"
    assert "error" in bt.task_create("api", "x", operation="rewrite")


def test_board_status_includes_operation(env):
    aos, b, _ = env
    bt = BoardTools(aos)
    bt.task_create("api", "Fix rounding", feature="checkout", operation="fix")
    bt.task_create("api", "Plain", feature="checkout")
    rows = {t["title"]: t for t in bt.board_status("checkout")["tasks"]}
    assert rows["Fix rounding"]["operation"] == "fix" and rows["Plain"]["operation"] is None


# -- worker and reviewer context ------------------------------------------------------
@pytest.mark.parametrize("op", OPERATIONS)
def test_worker_context_has_the_first_move(env, op):
    aos, b, _ = env
    ctx = build_worker_context(aos, b, b.task(b.add_task("checkout", "api", "a", operation=op)))
    assert f"## How to do this {op}" in ctx and FIRST_MOVE[op] in ctx


def test_fix_worker_must_name_the_test_in_its_summary(env):
    aos, b, _ = env
    ctx = build_worker_context(aos, b, b.task(b.add_task("checkout", "api", "a", operation="fix")))
    assert "task_complete" in ctx and "failed before the fix" in ctx


def test_worker_context_without_operation_has_no_section(env):
    aos, b, _ = env
    ctx = build_worker_context(aos, b, b.task(b.add_task("checkout", "api", "a")))
    assert "## How to do this" not in ctx
    assert not any(text in ctx for text in FIRST_MOVE.values())


def claimed_for_review(b, **kw):
    tid = b.add_task("checkout", "api", "a", **kw)
    b.promote()
    b.claim(tid)
    b.complete(tid, "did it", author="w", to="review")
    return tid


@pytest.mark.parametrize("op", OPERATIONS)
def test_reviewer_context_has_the_check(env, op):
    aos, b, proj = env
    ctx = build_review_context(aos, b, b.task(claimed_for_review(b, operation=op)), proj)
    assert f"## Review this {op}" in ctx and CHECK[op] in ctx


def test_reviewer_fails_fix_without_regression_test_and_weakened_refactor(env):
    aos, b, proj = env
    fix = build_review_context(aos, b, b.task(claimed_for_review(b, operation="fix")), proj)
    ref = build_review_context(aos, b, b.task(claimed_for_review(b, operation="refactor")), proj)
    assert "review_fail" in fix.split("## Review this fix")[1].split("\n## ")[0]
    assert "review_fail" in ref.split("## Review this refactor")[1].split("\n## ")[0]


def test_reviewer_context_without_operation_has_no_section(env):
    aos, b, proj = env
    ctx = build_review_context(aos, b, b.task(claimed_for_review(b)), proj)
    assert "## Review this" not in ctx
    assert not any(text in ctx for text in CHECK.values())
