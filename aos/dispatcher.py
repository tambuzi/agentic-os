"""`aos board run`: launch ready tasks as headless workers, in parallel across projects.

Each tick: reap finished/timed-out/cancelled workers, promote tasks whose
dependencies are done, then launch ready tasks up to the parallel limit with at
most one running task per (feature, project) worktree.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
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


@dataclass
class Running:
    proc: subprocess.Popen
    launch: Launch
    deadline: float
    timeout_min: float
    log: IO
    exit_reasons: dict
    timed_out: bool = False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Dispatcher:
    def __init__(self, repo: str | Path, *, feature: str | None = None, parallel: int | None = None,
                 tick: float = 2.0):
        self.aos = AOS(repo)
        board_cfg = self.aos.settings["board"]
        self.board = Board(self.aos.data, board_cfg["max_tasks_per_feature"])
        self.feature = feature
        self.parallel = int(parallel or board_cfg["parallel"])
        self.tick_s = tick
        self.running: dict[int, Running] = {}
        self.stopping = False
        self.aos_bin = shutil.which("aos") or "aos"

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
        """Tasks left `running` by a previous dispatcher: stop any survivor, count a failed attempt."""
        for t in self.board.tasks(status="running"):
            if t["pid"] and _alive(t["pid"]):
                try:
                    os.killpg(t["pid"], signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            self.board.attempt_failed(t["id"], "dispatcher restarted; previous worker abandoned")

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
        self.board.promote()
        if not self.stopping:
            self._launch_ready()

    def _terminate(self, r: Running) -> None:
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            try:
                os.killpg(r.proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                r.proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue

    def _reap(self) -> None:
        for tid, r in list(self.running.items()):
            code = r.proc.poll()
            if code is None:
                status = self.board.task(tid)["status"]
                if status == "cancelled":
                    self._terminate(r)
                elif time.monotonic() > r.deadline:
                    r.timed_out = True
                    self._terminate(r)
                code = r.proc.poll()
                if code is None:
                    continue
            r.launch.cleanup()
            r.log.close()
            del self.running[tid]
            if self.board.task(tid)["status"] != "running":
                continue
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
        busy = {(t["feature"], t["project"]) for t in self.board.tasks(status="running")}
        for t in self.board.tasks(feature=self.feature, status="ready"):
            if len(self.running) >= self.parallel:
                break
            key = (t["feature"], t["project"])
            if key in busy:
                continue
            busy.add(key)
            self._launch(t)

    def _launch(self, t: dict) -> None:
        tid = t["id"]
        try:
            project_path = linked_project_path(t["project"])
            wt = ensure_worktree(project_path, worktree_path(self.aos.home, t["feature"], t["project"]),
                                 branch_name(t["feature"]))
        except AosError as e:
            self.board.block(tid, e.message + (f" ({e.hint})" if e.hint else ""), author="dispatcher")
            return
        task = self.board.claim(tid)
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
        self.board.set_process(tid, proc.pid, launch.session_id)
        timeout = float(settings["timeout_min"])
        self.running[tid] = Running(proc, launch, time.monotonic() + 60 * timeout, timeout, log,
                                    getattr(mod, "EXIT_REASONS", {}))
