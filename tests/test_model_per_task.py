"""Model per task: set by hand (task > feature > project) or picked from the task's size (auto),
else the tool's default (ROADMAP 3b + task sizing)."""

import pytest
import yaml

from aos.board import Board
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.errors import AosError
from aos.mcp_server import BoardTools
from aos.workers.common import check_model, prepare_review, prepare_run, profile_settings, resolve_model


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def env(configured, data, git_repo, tmp_path):
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]},
                                                          "web": {"path": str(proj), "targets": ["claude"]}}})
    aos = AOS(configured, slug="api")
    aos.register_project("api")
    aos.register_project("web")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    return aos, b, proj, tmp_path


def project_model(aos, project, model):
    p = aos.data / "projects.yaml"
    d = yaml.safe_load(p.read_text())
    d["projects"][project]["worker"] = {"model": model}
    p.write_text(yaml.safe_dump(d))


def test_check_model_fits_the_tool(env):
    aos = env[0]
    claude, kiro = profile_settings(aos, "claude", "api"), profile_settings(aos, "kiro", "api")
    for ok in ("opus", "sonnet", "haiku", "fable", "claude-opus-5-5"):
        check_model(claude, "claude", ok)
    with pytest.raises(AosError, match="claude"):
        check_model(claude, "claude", "gpt-5")
    check_model(kiro, "kiro", "claude-sonnet-4.5")           # Kiro: its own names, or a configured list
    with pytest.raises(AosError, match="kiro"):
        check_model(kiro, "kiro", "opus")                      # a Claude short name means nothing to Kiro
    with pytest.raises(AosError, match="kiro"):
        check_model({**kiro, "models": ["auto", "claude-sonnet-4.5"]}, "kiro", "opus")


def test_precedence_task_feature_project_size_default(env):
    aos, b, *_ = env
    t = b.task(b.add_task("checkout", "api", "a", worker="claude"))
    assert resolve_model(aos, t) == ("sonnet", "tool default")
    t = b.task(b.add_task("checkout", "api", "b", worker="claude", size="large"))
    assert resolve_model(aos, t) == ("opus", "size large")
    t = b.task(b.add_task("checkout", "api", "c", worker="claude", size="trivial"))
    assert resolve_model(aos, t) == ("haiku", "size trivial")
    project_model(aos, "api", "haiku")
    assert resolve_model(aos, b.task(t["id"])) == ("haiku", "project api")
    t = b.task(b.add_task("checkout", "api", "d", worker="claude", size="large"))
    assert resolve_model(aos, t) == ("haiku", "project api")               # by hand beats size
    b.create_feature("pay", "Pay", model="opus")
    t = b.task(b.add_task("pay", "api", "e", worker="claude"))
    assert resolve_model(aos, t) == ("opus", "feature")
    t = b.task(b.add_task("pay", "api", "f", worker="claude", model="sonnet"))
    assert resolve_model(aos, t) == ("sonnet", "task")


def test_kiro_auto_without_tiers(env):
    aos, b, *_ = env
    t = b.task(b.add_task("checkout", "api", "k", worker="kiro", size="large"))
    assert resolve_model(aos, t) == (None, "Kiro's auto")


def test_a_project_model_for_the_other_tool_is_ignored(env):
    aos, b, *_ = env
    project_model(aos, "api", "opus")                         # meant for the project's Claude tasks
    t = b.task(b.add_task("checkout", "api", "k", worker="kiro"))
    assert resolve_model(aos, t) == (None, "Kiro's auto")


def test_the_model_reaches_workers_and_reviewers(env):
    aos, b, proj, tmp_path = env
    tid = b.add_task("checkout", "api", "a", worker="claude", size="large")
    b.promote()
    task = b.claim(tid)
    spec, settings = prepare_run(aos, b, task, proj, proj, "/bin/aos")
    assert spec.model == "opus" and settings["model"] == "opus"
    from aos.workers import claude
    assert claude.prepare(spec, settings).argv[claude.prepare(spec, settings).argv.index("--model") + 1] == "opus"
    rspec, _ = prepare_review(aos, b, task, proj, proj, "/bin/aos")
    assert rspec.model == "opus"


def test_cli_flags_and_display(env, capsys):
    aos, b, *_ = env
    assert run(["task", "add", "checkout", "api", "small one", "--size", "trivial"], capsys)[0] == 0
    code, out, _ = run(["task", "add", "checkout", "api", "big one", "--model", "opus", "--size", "large"], capsys)
    assert code == 0 and "opus" in out
    code, _, err = run(["task", "add", "checkout", "api", "bad", "--model", "gpt-5"], capsys)
    assert code != 0 and "gpt-5" in err
    with pytest.raises(SystemExit):
        main(["task", "add", "checkout", "api", "bad size", "--size", "huge"])
    capsys.readouterr()
    code, out, _ = run(["board"], capsys)
    assert "claude/haiku/" in out and "claude/opus/" in out
    code, out, _ = run(["task", "show", "2"], capsys)
    assert "model: opus (task)" in out and "size: large" in out
    assert run(["feature", "new", "pay", "--title", "Pay", "--projects", "api", "--model", "haiku"], capsys)[0] == 0
    assert b.feature("pay")["model"] == "haiku"


def test_retry_on_a_stronger_model(env, capsys):
    aos, b, *_ = env
    tid = b.add_task("checkout", "api", "a", worker="claude")
    b.promote()
    b.claim(tid)
    b.block(tid, "too hard", author="w")
    assert run(["task", "retry", str(tid), "--model", "opus"], capsys)[0] == 0
    assert b.task(tid)["model"] == "opus"
    assert run(["task", "retry", str(tid), "--model", "nope"], capsys)[0] != 0


def test_planner_tools_take_size_and_model(env):
    aos, b, *_ = env
    bt = BoardTools(aos)
    r = bt.task_create("api", "Add mul", feature="checkout", size="trivial")
    assert b.task(r["task"])["size"] == "trivial" and r["model"] == "haiku"
    r = bt.task_create("api", "Redesign auth", feature="checkout", size="large", model="opus")
    assert b.task(r["task"])["model"] == "opus"
    assert "error" in bt.task_create("api", "x", feature="checkout", size="enormous")
    assert "error" in bt.task_create("api", "x", feature="checkout", model="gpt-5")
    assert bt.feature_create("pay", "Pay", model="sonnet")["slug"] == "pay"
    rows = {t["title"]: t for t in bt.board_status("checkout")["tasks"]}
    assert rows["Add mul"]["model"] == "haiku" and rows["Add mul"]["model_source"] == "size trivial"
    assert rows["Add mul"]["size"] == "trivial"


def test_board_run_model_override_and_tool_switch(env):
    from aos.dispatcher import Dispatcher
    aos, b, *_ = env
    tid = b.add_task("checkout", "api", "a", worker="claude", model="opus")
    d = Dispatcher(aos.repo, worker="kiro")
    d._apply_run_overrides(b.task(tid))
    t = b.task(tid)
    assert t["worker"] == "kiro" and t["model"] is None        # opus doesn't fit Kiro: reset, with an event
    assert any("model reset" in e["body"] for e in b.task_events(tid))
    d = Dispatcher(aos.repo, model="haiku")
    t2 = b.add_task("checkout", "api", "b", worker="claude", model="opus")
    launched = d._run_model(b.task(t2))
    assert launched["model"] == "haiku"
    assert b.task(t2)["model"] == "opus"          # review: --model is for this run only, never stored


def test_skill_tells_the_planner_to_size_tasks():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "skills/aos-feature/SKILL.md").read_text()
    assert "size" in text and "trivial" in text and "large" in text and "model" in text


def test_fit_check_tells_kiro_and_claude_ids_apart(env):
    # review: claude-… matched both tools, so a Kiro id could launch Claude and vice versa
    aos = env[0]
    claude, kiro = profile_settings(aos, "claude", "api"), profile_settings(aos, "kiro", "api")
    with pytest.raises(AosError):
        check_model(claude, "claude", "claude-sonnet-4.5")        # Kiro's dotted style
    with pytest.raises(AosError):
        check_model(kiro, "kiro", "claude-opus-4-1-20250805")      # a Claude API id
    check_model(claude, "claude", "claude-opus-4-1-20250805")
    check_model(kiro, "kiro", "claude-sonnet-4.5")


def test_model_is_stripped_and_auto_clears(env, capsys):
    aos, b, *_ = env
    assert run(["task", "add", "checkout", "api", "a", "--model", " opus "], capsys)[0] == 0
    assert b.task(1)["model"] == "opus"
    assert run(["task", "add", "checkout", "api", "b", "--model", "auto", "--size", "trivial"], capsys)[0] == 0
    assert b.task(2)["model"] is None and resolve_model(aos, b.task(2))[0] == "haiku"
    b.promote()
    b.claim(1)
    b.block(1, "x", author="w")
    assert run(["task", "retry", "1", "--model", "auto"], capsys)[0] == 0
    assert b.task(1)["model"] is None


def test_retry_with_another_worker_resets_a_misfit_model(env, capsys):
    aos, b, *_ = env
    tid = b.add_task("checkout", "api", "a", worker="claude", model="opus")
    b.promote()
    b.claim(tid)
    b.block(tid, "x", author="w")
    assert run(["task", "retry", str(tid), "--worker", "kiro"], capsys)[0] == 0
    t = b.task(tid)
    assert t["worker"] == "kiro" and t["model"] is None
    assert any("model reset" in e["body"] for e in b.task_events(tid))


def test_board_run_rejects_an_unknown_worker(env, capsys):
    from aos.dispatcher import Dispatcher
    aos, b, *_ = env
    tid = b.add_task("checkout", "api", "a", worker="claude")
    with pytest.raises(AosError, match="unknown worker profile 'foo'"):
        Dispatcher(aos.repo, worker="foo")
    code, out, err = run(["board", "run", "--once", "--worker", "foo"], capsys)
    assert code == 1 and "unknown worker profile" in err and "dispatching" not in out
    assert b.task(tid)["worker"] == "claude"


def test_board_run_worker_keeps_the_tool_of_a_resumed_task(env):
    from aos.dispatcher import Dispatcher
    aos, b, *_ = env
    tid = b.add_task("checkout", "api", "a", worker="claude")
    b.promote()
    b.claim(tid)
    b.block(tid, "x", author="w")
    b.retry(tid, resume=True)
    t = Dispatcher(aos.repo, worker="kiro")._apply_run_overrides(b.task(tid))
    assert t["worker"] == "claude" and b.task(tid)["worker"] == "claude"
    assert any("resume" in e["body"] and "kiro" in e["body"] for e in b.task_events(tid))
