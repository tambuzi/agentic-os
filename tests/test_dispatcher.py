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


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_worker_outliving_complete_keeps_worktree_busy(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "complete_then_sleep")
    a = add(board, "api")
    b = add(board, "api", deps=[a])
    d = Dispatcher(repo, tick=0.05)
    d.grace_s = 0.5
    try:
        d.tick()
        assert wait_for(lambda: board.task(a)["status"] == "done")
        d.tick()
        assert list(d.running) == [a] and board.task(b)["status"] == "ready"
        time.sleep(0.6)
        d.tick()
        assert a not in d.running
        d.tick()
        assert b in d.running
    finally:
        stop_all(d)


def test_unblocked_task_not_relaunched_while_old_process_lives(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "block_then_sleep")
    t = add(board, "api")
    d = Dispatcher(repo, tick=0.05)
    d.grace_s = 60
    try:
        d.tick()
        old = d.running[t].proc
        assert wait_for(lambda: board.task(t)["status"] == "blocked")
        board.unblock(t)
        d.tick()
        assert d.running[t].proc is old and old.poll() is None
    finally:
        stop_all(d)


def test_cancel_during_launch_does_not_crash(env, monkeypatch):
    repo, board, _, _ = env
    t = add(board, "api")
    import aos.dispatcher as disp
    real = disp.ensure_worktree

    def cancelling(*args, **kw):
        board.cancel(t)
        return real(*args, **kw)

    monkeypatch.setattr(disp, "ensure_worktree", cancelling)
    d = Dispatcher(repo, tick=0.05)
    d.tick()
    assert board.task(t)["status"] == "cancelled" and not d.running


def test_closed_feature_tasks_not_dispatched(env):
    repo, board, _, _ = env
    t = add(board, "api")
    board.set_feature_status("checkout", "cancelled")
    assert board.task(t)["status"] == "cancelled"
    board.create_feature("other", "Other")
    u = board.add_task("other", "web", "u", worker="fake")
    board.promote()
    board.set_feature_status("other", "done")
    d = Dispatcher(repo, tick=0.05)
    d.tick()
    assert not d.running and board.task(u)["status"] == "ready"


def test_recover_never_signals_a_reused_pid(env):
    repo, board, _, _ = env
    t = add(board, "api")
    board.promote()
    board.claim(t)
    stranger = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        board.set_process(t, stranger.pid, None, proc_start="Thu Jan  1 00:00:00 1970")
        Dispatcher(repo, tick=0.05).recover()
        assert stranger.poll() is None
        assert board.task(t)["status"] == "ready"
    finally:
        stranger.kill()
        stranger.wait()


def test_recover_kills_own_orphan(env):
    from aos.dispatcher import process_start
    repo, board, _, _ = env
    t = add(board, "api")
    board.promote()
    board.claim(t)
    orphan = subprocess.Popen(["sleep", "30"], start_new_session=True)
    board.set_process(t, orphan.pid, None, proc_start=process_start(orphan.pid))
    Dispatcher(repo, tick=0.05).recover()
    assert orphan.wait(timeout=10) is not None


def test_terminating_own_child_is_fast():
    from aos.dispatcher import _kill_group
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    start = time.monotonic()
    _kill_group(p.pid, proc=p)
    assert p.poll() is not None and time.monotonic() - start < 1.5
