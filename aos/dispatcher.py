"""`aos board run`: launch ready tasks as headless workers, in parallel across projects.

Each tick: reap finished/timed-out/cancelled workers, promote tasks whose
dependencies are done, then launch ready tasks of open features up to the
parallel limit, with at most one live worker process per (feature, project)
worktree. A worker that outlives its task_complete/task_block keeps its
worktree busy for a short grace period and is then terminated.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .board import Board
from .core import AOS
from .errors import AosError
from .workers import adapter
from .workers.common import Launch, linked_project_path, prepare_run
from .worktrees import branch_name, ensure_worktree, worktree_path

# Board-level failures for a single task (status moved under us, sqlite busy, ...)
# are logged and skipped; they never stop the loop.
TASK_ERRORS = (AosError, sqlite3.Error)


@dataclass
class Running:
    proc: subprocess.Popen
    launch: Launch
    deadline: float
    timeout_min: float
    log: IO
    exit_reasons: dict
    feature: str
    project: str
    timed_out: bool = False
    finished_at: float | None = None  # when the task left `running` while the process lived on


def process_start(pid: int) -> str | None:
    """Start time of a process as `ps` reports it; identifies the process across pid reuse."""
    r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _kill_group(pgid: int, wait: float = 5.0, proc: subprocess.Popen | None = None) -> None:
    """SIGTERM a process group, wait for it to disappear, then SIGKILL.
    Pass our own child as `proc` so its zombie is reaped (a zombie still counts as a member)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if proc is not None:
                proc.poll()
            if not _group_alive(pgid):
                return
            time.sleep(0.05)


def _warn(msg: str) -> None:
    print(f"[aos board] {msg}", file=sys.stderr)


class Dispatcher:
    def __init__(self, repo: str | Path, *, feature: str | None = None, parallel: int | None = None,
                 tick: float = 2.0):
        self.aos = AOS(repo)
        board_cfg = self.aos.settings["board"]
        self.board = Board(self.aos.data, board_cfg["max_tasks_per_feature"])
        self.feature = feature
        self.parallel = int(parallel or board_cfg["parallel"])
        self.tick_s = tick
        self.grace_s = 30.0
        self.running: dict[int, Running] = {}
        self.stopping = False
        self.aos_bin = shutil.which("aos") or "aos"
        self._last_stuck: dict | None = None

    # -- lifecycle -----------------------------------------------------------
    @contextmanager
    def _lock(self):
        path = self.board.data / ".dispatcher.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise AosError("another `aos board run` is already running")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def recover(self) -> None:
        """Tasks left `running` by a previous dispatcher: stop the old worker only if it is
        provably ours (same pid *and* start time), then count a failed attempt."""
        for t in self.board.tasks(status="running"):
            pid = t["pid"]
            if pid and t.get("proc_start") and process_start(pid) == t["proc_start"]:
                _kill_group(pid)
            try:
                self.board.attempt_failed(t["id"], "dispatcher restarted; previous worker abandoned")
            except TASK_ERRORS as e:
                _warn(f"recover task #{t['id']}: {e}")

    def run(self, once: bool = False) -> None:
        with self._lock():
            self.recover()
            self.tick()
            if once:
                while self.running:
                    time.sleep(self.tick_s)
                    self._reap()
                self.board.promote()
                return
            previous = signal.signal(signal.SIGINT, self._on_sigint)
            try:
                while not (self.stopping and not self.running):
                    time.sleep(self.tick_s)
                    self.tick()
            finally:
                signal.signal(signal.SIGINT, previous)

    def _on_sigint(self, *_):
        if self.stopping:
            for r in list(self.running.values()):
                self._terminate(r)
        self.stopping = True

    # -- one tick ------------------------------------------------------------
    def tick(self) -> None:
        self._reap()
        try:
            self.board.promote()
        except TASK_ERRORS as e:
            _warn(f"promote: {e}")
        if not self.stopping:
            self._launch_ready()
        self._report_stuck()

    def _report_stuck(self) -> None:
        if self.running:
            return
        try:
            stuck = self.board.stuck()
        except TASK_ERRORS:
            return
        if stuck and stuck != self._last_stuck:
            _warn("nothing can run: " + "; ".join(
                f"#{t} waits on failed/cancelled " + ", ".join(f"#{d}" for d in deps) for t, deps in stuck.items())
                + "  (aos task retry <id> | aos task cancel <id>)")
        self._last_stuck = stuck

    def _terminate(self, r: Running) -> None:
        _kill_group(r.proc.pid, proc=r.proc)

    def _reap(self) -> None:
        for tid, r in list(self.running.items()):
            try:
                self._reap_one(tid, r)
            except TASK_ERRORS as e:
                _warn(f"task #{tid}: {e}")

    def _reap_one(self, tid: int, r: Running) -> None:
        code = r.proc.poll()
        if code is None:
            status = self.board.task(tid)["status"]
            now = time.monotonic()
            if status == "cancelled":
                self._terminate(r)
            elif now > r.deadline:
                r.timed_out = True
                self._terminate(r)
            elif status != "running":
                # completed/blocked but still alive: give it a moment to exit, then stop it
                r.finished_at = r.finished_at or now
                if now - r.finished_at >= self.grace_s:
                    self._terminate(r)
            code = r.proc.poll()
            if code is None:
                return
        # the leader exited; stop anything it left behind in its process group
        _kill_group(r.proc.pid, wait=1.0, proc=r.proc)
        r.launch.cleanup()
        r.log.close()
        del self.running[tid]
        if self.board.task(tid)["status"] != "running":
            return
        if r.timed_out:
            why = f"timed out after {r.timeout_min:g} min"
        elif code in r.exit_reasons:
            why = r.exit_reasons[code]
        elif code == 0:
            why = "exited without task_complete"
        else:
            why = f"exited with code {code}"
        self.board.attempt_failed(tid, why)

    def _launch_ready(self) -> None:
        try:
            busy = {(t["feature"], t["project"]) for t in self.board.tasks(status="running")}
            ready = self.board.dispatchable(self.feature)
        except TASK_ERRORS as e:
            _warn(f"board read: {e}")
            return
        busy |= {(r.feature, r.project) for r in self.running.values()}
        for t in ready:
            if len(self.running) >= self.parallel:
                break
            key = (t["feature"], t["project"])
            if t["id"] in self.running or key in busy:
                continue
            busy.add(key)
            try:
                self._launch(t)
            except TASK_ERRORS as e:
                _warn(f"task #{t['id']}: {e}")

    def _launch(self, t: dict) -> None:
        tid = t["id"]
        try:
            project_path = linked_project_path(t["project"])
            wt = ensure_worktree(project_path, worktree_path(self.aos.home, t["feature"], t["project"]),
                                 branch_name(t["feature"]))
        except AosError as e:
            self.board.block(tid, e.message + (f" ({e.hint})" if e.hint else ""), author="dispatcher")
            return
        task = self.board.claim(tid)  # raises if the task moved (e.g. cancelled) meanwhile
        launch = None
        try:
            aos = AOS(self.aos.repo, home=self.aos.home, slug=t["project"], data=self.aos.data)
            spec, settings = prepare_run(aos, self.board, task, wt, project_path, self.aos_bin)
            mod = adapter(settings["adapter"])
            launch = mod.prepare(spec, settings)
            log_path = self.board.data / "logs" / f"{tid}-{task['attempts']}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = open(log_path, "ab")
            proc = subprocess.Popen(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except Exception as e:  # any launch failure is a failed attempt, never a crash of the loop
            if launch:
                launch.cleanup()
            self.board.attempt_failed(tid, f"launch failed: {e}")
            return
        timeout = float(settings["timeout_min"])
        self.running[tid] = Running(proc, launch, time.monotonic() + 60 * timeout, timeout, log,
                                    getattr(mod, "EXIT_REASONS", {}), t["feature"], t["project"])
        self.board.set_process(tid, proc.pid, launch.session_id, proc_start=process_start(proc.pid))
