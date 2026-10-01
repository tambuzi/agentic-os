import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.errors import AosError

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


@pytest.fixture
def env(configured, data, home, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"default_worker": "fake", "parallel": 2},
        "workers": {"fake": {"adapter": "command", "timeout_min": 0.02,
                             "command": [sys.executable, str(FAKE), "{task_id}"]}}}))
    projects = {}
    for name in ("api", "web", "billing"):
        projects[name] = {"path": str(git_repo(tmp_path / name)), "targets": ["claude"]}
        AOS(configured).register_project(name)
    save_user_config({**load_user_config(), "projects": projects})
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    return configured, board, tmp_path, home


def add(board, project, title="t", deps=(), max_attempts=2):
    return board.add_task("checkout", project, title, depends_on=deps, worker="fake", max_attempts=max_attempts)


def stop_all(d):
    for r in list(d.running.values()):
        d._terminate(r)
    d._reap()


def test_parallel_cap_and_one_per_worktree(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "sleep")
    ids = [add(board, p) for p in ("api", "web", "billing", "api")]
    d = Dispatcher(repo, tick=0.05)
    try:
        d.tick()
        assert len(d.running) == 2
        d.parallel = 4
        d.tick()
        running = {board.task(t)["project"] for t in d.running}
        assert len(d.running) == 3 and running == {"api", "web", "billing"}
        assert board.task(ids[3])["status"] == "ready"
    finally:
        stop_all(d)


def test_once_runs_dependency_chain(env):
    repo, board, tmp_path, home = env
    a = add(board, "api")
    b = add(board, "web", deps=[a])
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(a)["status"] == "done" and board.task(b)["status"] == "ready"
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(b)["status"] == "done"
    assert "done by fake worker in" in board.task(b)["result"]
    assert (home / "worktrees/checkout/web").is_dir()
    head = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path / "web",
                          capture_output=True, text=True).stdout.strip()
    assert head == "main"


def test_worker_writes_only_in_worktree(env, monkeypatch):
    repo, board, tmp_path, home = env
    monkeypatch.setenv("FAKE_MODE", "write")
    t = add(board, "api")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert (home / f"worktrees/checkout/api/t{t}.txt").exists()
    assert not (tmp_path / f"api/t{t}.txt").exists()


def test_failures_retry_then_failed(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "crash")
    t = add(board, "api", max_attempts=2)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(t)["status"] == "ready" and board.task(t)["attempts"] == 1
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(t)["status"] == "failed"
    assert any("exited with code 2" in e["body"] for e in board.events("checkout"))
    assert list((board.data / "logs").glob(f"{t}-*.log"))


def test_exit_zero_without_complete(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "noop")
    t = add(board, "api", max_attempts=1)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(t)["status"] == "failed"
    assert any("exited without task_complete" in e["body"] for e in board.events("checkout"))


def test_timeout_kills_worker(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "sleep")
    t = add(board, "api", max_attempts=1)
    start = time.monotonic()
    Dispatcher(repo, tick=0.05).run(once=True)
    assert time.monotonic() - start < 10
    assert board.task(t)["status"] == "failed"
    assert any("timed out" in e["body"] for e in board.events("checkout"))


def test_block_unlinked_project_and_worker_block(env, monkeypatch):
    repo, board, _, _ = env
    ghost = add(board, "ghost")
    monkeypatch.setenv("FAKE_MODE", "block")
    t = add(board, "api")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(ghost)["status"] == "blocked"
    assert any("not linked" in e["body"] for e in board.events("checkout") if e["task"] == ghost)
    assert board.task(t)["status"] == "blocked"


def test_cancel_terminates_running_worker(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "sleep")
    t = add(board, "api")
    d = Dispatcher(repo, tick=0.05)
    d.tick()
    proc = d.running[t].proc
    board.cancel(t)
    d.tick()
    assert proc.poll() is not None and t not in d.running
    assert board.task(t)["status"] == "cancelled"


def test_recover_marks_orphans(env):
    repo, board, _, _ = env
    t = add(board, "api")
    board.promote()
    board.claim(t)
    dead = subprocess.Popen(["true"])
    dead.wait()
    board.set_process(t, dead.pid, None)
    Dispatcher(repo, tick=0.05).recover()
    assert board.task(t)["status"] == "ready"
    assert any("restarted" in e["body"] for e in board.events("checkout"))


def test_single_dispatcher_lock(env):
    repo, _, _, _ = env
    d1 = Dispatcher(repo, tick=0.05)
    with d1._lock():
        with pytest.raises(AosError, match="already running"):
            Dispatcher(repo, tick=0.05).run(once=True)
