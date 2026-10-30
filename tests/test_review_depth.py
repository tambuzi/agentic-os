"""Review depth (ROADMAP 4b): trivial tasks skip the reviewer, sensitive changes get a
security-focused review, risky tasks can need two independent passes."""

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.mcp_server import BoardTools
from aos.review_focus import security_reasons
from aos.workers.common import build_review_context

ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]


# -- board: rounds of independent review -------------------------------------------
@pytest.fixture
def b(data):
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    return board


def claimed_for_review(b, **kw):
    tid = b.add_task("checkout", "api", "a", **kw)
    b.promote()
    b.claim(tid)
    b.complete(tid, "did it", author="w", to="review")
    b.mark_verified(tid, "tests pass")
    return tid


def test_two_passes_needed(b):
    tid = claimed_for_review(b)
    t = b.start_review(tid, required=2)
    b.review_verdict(tid, True, "looks right", generation=t["generation"])
    t = b.task(tid)
    assert t["status"] == "review" and t["review_stage"] == "verified" and t["reviews_passed"] == 1
    assert any("independent review 1 of 2 passed" in e["body"] for e in b.task_events(tid))
    t = b.start_review(tid, required=2)
    b.review_verdict(tid, True, "agreed", generation=t["generation"])
    assert b.task(tid)["status"] == "done"


def test_second_reviewer_can_still_reject(b):
    tid = claimed_for_review(b)
    t = b.start_review(tid, required=2)
    b.review_verdict(tid, True, "fine", generation=t["generation"])
    t = b.start_review(tid, required=2)
    b.review_verdict(tid, False, "SQL built from user input in orders.py:12", generation=t["generation"])
    t = b.task(tid)
    assert t["status"] == "ready" and t["reviews_passed"] == 0


def test_a_flaky_second_reviewer_does_not_skip_the_round(b):
    # review_unfinished accepts after two verdict-less reviews; that count must restart per round
    tid = claimed_for_review(b)
    t = b.start_review(tid, required=2)
    b.review_verdict(tid, True, "fine", generation=t["generation"])
    b.start_review(tid, required=2)
    b.review_unfinished(tid, "exited without a verdict")
    assert b.task(tid)["status"] == "review"           # retried, not accepted


# -- detection ---------------------------------------------------------------------
@pytest.mark.parametrize("paths,diff,flag,expect", [
    (["src/auth/session.py"], "", False, "auth"),
    (["config/.env.example"], "", False, ".env"),
    (["app/orders.py"], '+    password = request.args["pw"]', False, "password"),
    (["app/orders.py"], '+    cur.execute(f"SELECT * FROM t WHERE id={uid}")', False, "SQL"),
    (["app/run.py"], "+    subprocess.run(cmd, shell=True)", False, "shell"),
    (["app/x.py"], "+    eval(user_input)", False, "eval"),
    (["app/orders.py"], "+    total = a + b", True, "planner"),
])
def test_security_reasons(paths, diff, flag, expect):
    reasons = security_reasons(paths, diff, flagged=flag)
    assert reasons and any(expect.lower() in r.lower() for r in reasons), reasons


def test_plain_changes_are_not_security(b):
    assert security_reasons(["app/calc.py", "README.md"], "+def mul(a, b):\n+    return a * b", flagged=False) == []


def test_security_section_in_the_reviewer_context(configured, data, git_repo, tmp_path):
    proj = git_repo(tmp_path / "api")
    aos = AOS(configured, slug="api")
    aos.register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    tid = claimed_for_review(b)
    ctx = build_review_context(aos, b, b.task(tid), proj, security=["touches auth/session.py"])
    assert "## Security review" in ctx and "touches auth/session.py" in ctx and "path traversal" in ctx
    assert "## Security review" not in build_review_context(aos, b, b.task(tid), proj)


# -- dispatcher --------------------------------------------------------------------
@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    monkeypatch.setenv("FAKE_ACP_MODE", "complete")
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")

    def write(board_cfg=None):
        (configured / "aos.yaml").write_text(yaml.safe_dump({
            "board": {"review": True, "verify": True, **(board_cfg or {})},
            "learning": {"enabled": False},
            "workers": {"claude": {"transport": "acp", "acp_command": FAKE_ACP, "timeout_min": 1}}}))
    write()
    return configured, b, write, tmp_path


def roles(tmp_path):
    p = tmp_path / "agent.events"
    events = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
    return [e["data"].get("role") for e in events if e["kind"] == "session/new"]


def run_until_done(repo, b, tid, timeout=30):
    d = Dispatcher(repo, tick=0.05)
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end and b.task(tid)["status"] not in ("done", "failed"):
            d.tick()
            time.sleep(0.05)
    finally:
        for r in list(d.running.values()):
            d._terminate(r)
        d._reap()
    return b.task(tid)


def test_trivial_tasks_skip_the_reviewer(env):
    repo, b, write, tmp_path = env
    tid = b.add_task("checkout", "api", "rename", worker="claude", size="trivial")
    assert run_until_done(repo, b, tid)["status"] == "done"
    assert roles(tmp_path) == ["worker"]
    assert any("independent review skipped" in e["body"] for e in b.task_events(tid))


def test_skip_sizes_can_be_emptied(env):
    repo, b, write, tmp_path = env
    write({"skip_review_sizes": []})
    tid = b.add_task("checkout", "api", "rename", worker="claude", size="trivial")
    assert run_until_done(repo, b, tid)["status"] == "done"
    assert roles(tmp_path) == ["worker", "reviewer"]


def test_large_tasks_get_two_reviewers_when_configured(env):
    repo, b, write, tmp_path = env
    write({"second_review": ["large"]})
    tid = b.add_task("checkout", "api", "redesign", worker="claude", size="large")
    assert run_until_done(repo, b, tid)["status"] == "done"
    assert roles(tmp_path) == ["worker", "reviewer", "reviewer"]


def test_flagged_security_task_gets_the_security_review(env, data):
    repo, b, write, tmp_path = env
    write({"second_review": ["security"]})
    tid = b.add_task("checkout", "api", "login rate limit", worker="claude", security=True)
    assert run_until_done(repo, b, tid)["status"] == "done"
    assert roles(tmp_path) == ["worker", "reviewer", "reviewer"]
    assert any(e["body"].startswith("security review:") for e in b.task_events(tid))
    ctx = (Path(data) / "runs" / f"{tid}-review-1" / "context.md").read_text()
    assert "## Security review" in ctx


def test_flags_on_cli_and_mcp(configured, data, capsys):
    aos = AOS(configured, slug="api")
    aos.register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    assert main(["task", "add", "checkout", "api", "auth fix", "--security"]) == 0
    assert b.task(1)["security"] == 1
    r = BoardTools(aos).task_create("api", "token refresh", feature="checkout", security=True)
    assert b.task(r["task"])["security"] == 1
    capsys.readouterr()


# -- review fixes ------------------------------------------------------------------
def test_a_missing_worktree_does_not_crash_the_dispatcher(configured, data, git_repo, tmp_path):
    # review: review_needs ran git outside the try; FileNotFoundError escaped tick()
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    tid = claimed_for_review(b, worker="claude")
    d = Dispatcher(configured, tick=0.05)
    d._launch_reviews()                                   # no worktree exists: must not raise
    t = b.task(tid)
    assert t["status"] == "review" and t["review_stage"] == "verified"
    assert any("reviewer launch failed" in e["body"] for e in b.task_events(tid))


def test_trivial_but_security_still_gets_a_reviewer(env):
    repo, b, write, tmp_path = env
    tid = b.add_task("checkout", "api", "rotate key", worker="claude", size="trivial", security=True)
    assert run_until_done(repo, b, tid)["status"] == "done"
    assert roles(tmp_path) == ["worker", "reviewer"]


def test_the_second_reviewer_cannot_read_the_first_verdict(b):
    tid = claimed_for_review(b)
    t = b.start_review(tid, required=2)
    b.review_verdict(tid, True, "FIRST-VERDICT-TEXT", generation=t["generation"])
    t = b.start_review(tid, required=2)
    from aos.mcp_server import BoardTools
    aos = type("A", (), {"data": b.data, "settings": {"board": {"max_tasks_per_feature": 30}}})()
    bt = BoardTools(aos, task_id=tid, attempt=t["generation"], review=True)
    assert "FIRST-VERDICT-TEXT" not in json.dumps(bt.board_read())
    shown = bt.task_show()["task"]
    assert "reviews_passed" not in shown and "reviews_required" not in shown


@pytest.mark.parametrize("paths,diff", [
    (["src/tokenizer.py", "AUTHORS.md", "docs/author_guide.md"], ""),
    (["app/views.py"], '+    msg = "Please update your profile: " + name'),
    (["app/log.py"], '+    log.info("delete " + path)'),
    (["app/cache.py"], "+    key = hashlib.sha256(data).hexdigest()"),
    (["package-lock.json"], '+      "integrity": "sha1-abc"'),
    (["app/io.py"], "+    f = open(path, **kwargs)"),
    (["README.md"], "+Keep the secret sauce in the docs."),
])
def test_ordinary_changes_are_not_flagged(paths, diff):
    assert security_reasons(paths, diff) == []


@pytest.mark.parametrize("diff", [
    '+    sql = f"SELECT * FROM users WHERE id = {uid}"',
    "+    db.query(`SELECT * FROM users WHERE id = ${id}`)",
    '+    subprocess.check_output(["sh", "-c", cmd])',
    "+    h = hashlib.md5(password.encode())",
])
def test_obvious_cases_are_flagged(diff):
    assert security_reasons(["app/x.py"], diff) != []


def test_detection_is_fast_on_big_diffs():
    big = "\n".join("+CREATE TABLE t%d (id int); -- select from where update set delete from" % i
                    for i in range(12000))
    start = time.monotonic()
    security_reasons(["db/migrations/0001.sql"], big)
    assert time.monotonic() - start < 1.0


def test_review_rounds_get_their_own_log_and_run_dir(b, data):
    from aos.workers.common import review_run_name
    tid = claimed_for_review(b)
    t = b.start_review(tid, required=2)
    first = review_run_name(t)
    b.review_verdict(tid, True, "ok", generation=t["generation"])
    second = review_run_name(b.start_review(tid, required=2))
    assert first == "1" and second != first


def test_flaky_round_two_message_is_honest(b):
    tid = claimed_for_review(b)
    t = b.start_review(tid, required=2)
    b.review_verdict(tid, True, "ok", generation=t["generation"])
    for _ in range(2):
        b.start_review(tid, required=2)
        b.review_unfinished(tid, "no verdict")
    assert b.task(tid)["status"] == "done"
    assert any("1 of 2 independent reviews passed" in e["body"] for e in b.task_events(tid))
