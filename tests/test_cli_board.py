import sys
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"default_worker": "fake"},
        "workers": {"fake": {"adapter": "command", "timeout_min": 0.05,
                             "command": [sys.executable, str(FAKE), "{task_id}"]}}}))
    projects = {}
    for name in ("api", "web"):
        projects[name] = {"path": str(git_repo(tmp_path / name)), "targets": ["claude"]}
        AOS(configured).register_project(name)
    save_user_config({**load_user_config(), "projects": projects})
    return configured, Board(data)


def test_feature_and_task_flow(env, capsys):
    _, board = env
    code, out, _ = run(["feature", "new", "checkout", "--title", "Checkout v2", "--projects", "api,web"], capsys)
    assert code == 0 and "brief.md" in out
    assert "- api" in board.brief("checkout")
    assert run(["feature", "new", "x", "--title", "X", "--projects", "ghost"], capsys)[0] == 1
    code, out, _ = run(["task", "add", "checkout", "api", "Orders endpoint"], capsys)
    assert code == 0 and "#1" in out
    assert run(["task", "add", "checkout", "web", "Use it", "--after", "1"], capsys)[0] == 0
    assert board.task(2)["depends_on"] == [1] and board.task(2)["worker"] == "fake"
    code, out, _ = run(["feature", "list"], capsys)
    assert "checkout" in out
    code, out, _ = run(["board"], capsys)
    assert "Orders endpoint" in out and "todo" in out
    code, out, _ = run(["board", "run", "--once"], capsys)
    assert code == 0 and board.task(1)["status"] == "done"
    code, out, _ = run(["task", "show", "1", "--log"], capsys)
    assert "done by fake worker" in out
    code, out, _ = run(["task", "list", "--status", "ready"], capsys)
    assert "Use it" in out


def test_proposals_and_task_controls(env, capsys):
    _, board = env
    run(["feature", "new", "checkout", "--title", "Checkout"], capsys)
    run(["task", "add", "checkout", "api", "Orders"], capsys)
    p = board.propose(1, "add currency", "multi-currency", author="task:1")
    code, out, _ = run(["feature", "show", "checkout"], capsys)
    assert f"#{p}" in out and "add currency" in out
    assert run(["feature", "approve", str(p)], capsys)[0] == 0
    assert board.contract("checkout")["version"] == 2
    p2 = board.propose(1, "drop", "no", author="task:1")
    assert run(["feature", "reject", str(p2), "--reason", "keep"], capsys)[0] == 0
    assert run(["task", "cancel", "1"], capsys)[0] == 0
    assert run(["task", "retry", "1", "--note", "again"], capsys)[0] == 0
    assert board.task(1)["status"] == "ready"
    board.block(1, "need key", author="task:1")
    code, out, _ = run(["board"], capsys)
    assert "need key" in out
    assert run(["task", "unblock", "1", "--note", "key in vault"], capsys)[0] == 0
    assert run(["feature", "done", "checkout"], capsys)[0] == 0
    assert run(["task", "show", "99"], capsys)[0] == 1


def test_board_shows_stuck_tasks(env, capsys):
    _, board = env
    run(["feature", "new", "checkout", "--title", "Checkout"], capsys)
    run(["task", "add", "checkout", "api", "a"], capsys)
    run(["task", "add", "checkout", "web", "b", "--after", "1"], capsys)
    run(["task", "cancel", "1"], capsys)
    code, out, _ = run(["board"], capsys)
    assert "waiting on failed/cancelled #1" in out


def test_doctor_reports_worker_clis(configured, capsys, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    code, out, _ = run(["doctor"], capsys)
    assert "claude" in out and "kiro-cli" in out


def test_non_numeric_ids_and_bad_parallel_are_usage_errors(env, capsys):
    for argv in (["task", "show", "abc"], ["feature", "approve", "x"], ["board", "run", "--parallel", "0"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2
        assert "Traceback" not in capsys.readouterr().err


@pytest.fixture
def editor(tmp_path):
    """An $EDITOR with arguments: `python3 <script> <mode>`; mode write|fail|empty."""
    script = tmp_path / "ed.py"
    script.write_text("import sys, pathlib\nmode, path = sys.argv[1], pathlib.Path(sys.argv[2])\n"
                      "draft = path.read_text()\n"
                      "if mode == 'fail': sys.exit(1)\n"
                      "path.write_text('' if mode == 'empty' else draft.replace('add currency', 'add currency (EUR only)'))\n")
    return lambda mode: f"{sys.executable} {script} {mode}"


def test_approve_edit(env, capsys, monkeypatch, editor):
    _, board = env
    run(["feature", "new", "checkout", "--title", "Checkout"], capsys)
    run(["task", "add", "checkout", "api", "Orders"], capsys)
    for mode in ("fail", "empty"):
        p = board.propose(1, "add currency", "r", author="task:1")
        monkeypatch.setenv("EDITOR", editor(mode))
        code, _, err = run(["feature", "approve", str(p), "--edit"], capsys)
        assert code == 1 and "not approved" in err
        assert board.contract("checkout")["version"] == 1
        board.reject(p)
    p = board.propose(1, "add currency", "r", author="task:1")
    monkeypatch.setenv("EDITOR", editor("write"))
    assert run(["feature", "approve", str(p), "--edit"], capsys)[0] == 0
    c = board.contract("checkout")
    assert c["version"] == 2 and "add currency (EUR only)" in c["text"]
    assert f"## Change v2 (proposal #{p})" in c["text"]


def test_feature_workflow_command(env, capsys, tmp_path):
    import yaml as _yaml
    _, board = env
    run(["feature", "new", "checkout", "--title", "Checkout"], capsys)
    run(["task", "add", "checkout", "api", "Orders"], capsys)
    code, out, _ = run(["feature", "workflow", "checkout", "--out", str(tmp_path / "ws")], capsys)
    path = tmp_path / "ws/.kiro/workflows/aos-checkout.workflow.yaml"
    assert code == 0 and str(path) in out
    assert _yaml.safe_load(path.read_text())["steps"][1]["branches"][0]["id"] == "task-1"
    assert run(["feature", "workflow", "checkout", "--poll", "5", "--out", str(tmp_path)], capsys)[0] == 1
