"""`aos board run`: launch ready tasks as headless workers, in parallel across projects.

Each tick: reap finished/timed-out/cancelled workers, promote tasks whose
dependencies are done, then launch ready tasks of open features up to the
parallel limit, with at most one live worker process per (feature, project)
worktree. A worker that outlives its task_complete/task_block keeps its
worktree busy for a short grace period and is then terminated.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .board import Board
from .core import AOS
from .errors import AosError
from .workers import adapter
from .workers.common import (Launch, add_allowed_tool, linked_project_path, parse_cost, prepare_review,
                             prepare_run)
from .acp.permissions import decide
from .acp.runner import AcpRun
from .acp.tools import AdapterMissing, option_for
from .sysmem import available_gb
from .verify import head_commit, revert_to, verify_claim
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
    kind: str = "worker"              # or "reviewer"
    started_at: float = 0.0           # monotonic; recent starts reserve memory they haven't used yet
    log_path: Path | None = None      # the run's output, read for its cost when it exits
    acp: AcpRun | None = None         # live ACP session (None = one-shot CLI process)
    allowed: list | None = None       # neutral allow-list, for ACP permission requests
    worktree: Path | None = None
    generation: int = 0               # the attempt token it was launched with


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


LOCK_NAME = ".dispatcher.lock"


def dispatcher_status(data: str | Path) -> dict:
    """Is a dispatcher running? Probes the lock without taking it for longer than a moment."""
    path = Path(data) / LOCK_NAME
    if not path.exists():
        return {"running": False}
    with open(path, "r+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                info = json.loads(fh.read() or "{}")
            except json.JSONDecodeError:
                info = {}
            return {"running": True, "feature": info.get("feature"), "pid": info.get("pid"),
                    "started": info.get("started")}
        fcntl.flock(fh, fcntl.LOCK_UN)
    return {"running": False}


def _warn(msg: str) -> None:
    print(f"[aos board] {msg}", file=sys.stderr)


class Dispatcher:
    def __init__(self, repo: str | Path, *, feature: str | None = None, parallel: int | None = None,
                 tick: float = 2.0, worker: str | None = None):
        self.aos = AOS(repo)
        board_cfg = self.aos.settings["board"]
        self.board = Board(self.aos.data, board_cfg["max_tasks_per_feature"])
        self.feature = feature
        self.worker = worker  # run override: launch every task with this tool
        setting = parallel if parallel is not None else board_cfg["parallel"]
        # `auto`: the ceiling is max_parallel; free memory decides each start (see _may_start)
        self.parallel = int(board_cfg.get("max_parallel", 6) if str(setting) == "auto" else setting)
        if self.parallel < 1:
            raise AosError("parallel must be at least 1")
        self.worker_gb = float(board_cfg.get("worker_memory_gb", 1.0))
        self.min_free_gb = float(board_cfg.get("min_free_memory_gb", 2.0))
        self.settle_s = 60.0
        self._held_reason = ""
        self.stagger_s = float(board_cfg.get("start_stagger_sec", 2.0))
        self._last_start = float("-inf")
        self.tick_s = tick
        self.grace_s = 30.0
        self.running: dict[int, Running] = {}
        self.verifying: dict[int, Future] = {}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aos-verify")
        self.stopping = False
        self.once = False
        self.aos_bin = shutil.which("aos") or "aos"
        self._last_stuck: dict | None = None
        # permission id -> (task, Running, json-rpc request id, options, rule, asked at)
        self.pending_perms: dict[int, tuple] = {}

    # -- lifecycle -----------------------------------------------------------
    @contextmanager
    def _lock(self):
        path = self.board.data / LOCK_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")  # never truncate before holding the lock: the holder's info lives here
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise AosError("another `aos board run` is already running")
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "feature": self.feature,
                             "started": time.strftime("%Y-%m-%dT%H:%M:%S")}))
        fh.flush()
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
            self._remove_kiro_agent(t)
            try:
                self.board.attempt_failed(t["id"], "dispatcher restarted; previous worker abandoned")
            except TASK_ERRORS as e:
                _warn(f"recover task #{t['id']}: {e}")

    def _remove_kiro_agent(self, t: dict) -> None:
        """A crashed dispatcher never ran cleanup: drop the task's per-run Kiro agent file."""
        from .workers import kiro
        from .workers.common import profile_settings
        try:
            aos = AOS(self.aos.repo, home=self.aos.home, slug=t["project"], data=self.aos.data)
            settings = profile_settings(aos, t["worker"], t["project"])
        except AosError:
            return
        if settings.get("adapter") == "kiro":
            (kiro.agents_dir(settings) / f"{kiro.agent_name(t)}.json").unlink(missing_ok=True)

    def idle(self) -> bool:
        """Nothing running and nothing that can start: the rest needs a human (blocked,
        stuck, failed) or the feature is finished."""
        return (not self.running and not self.verifying and not self.board.dispatchable(self.feature)
                and not self.board.reviewable(self.feature))

    def run(self, once: bool = False, until_done: bool = False) -> None:
        self.once = once
        with self._lock():
            # handler first, so Ctrl-C at any point stops our workers instead of orphaning them
            previous = signal.signal(signal.SIGINT, self._on_sigint)
            try:
                self.recover()
                self.tick()
                if once:
                    # finish what this round started, including verifying its claims
                    while self.running or self.verifying or self.board.reviewable(self.feature):
                        time.sleep(self.tick_s)
                        self._service_acp()
                        self._resolve_permissions()
                        self._reap()
                        self._collect_verifications()
                        self._start_verifications()
                        if not self.stopping:
                            self._launch_reviews()
                    self.board.promote()
                    return
                while not (self.stopping and not self.running):
                    if until_done and self.idle():
                        return
                    time.sleep(self.tick_s)
                    self.tick()
            finally:
                signal.signal(signal.SIGINT, previous)

    def _on_sigint(self, *_):
        """--once: stop the workers now. Loop mode: first Ctrl-C stops launching, second stops workers."""
        if self.once or self.stopping:
            for r in list(self.running.values()):
                self._terminate(r)
        self.stopping = True

    # -- one tick ------------------------------------------------------------
    def tick(self) -> None:
        self._service_acp()
        self._resolve_permissions()
        self._reap()
        self._collect_verifications()
        try:
            self.board.promote()
        except TASK_ERRORS as e:
            _warn(f"promote: {e}")
        if not self.stopping:
            self._launch_ready()
            self._launch_reviews()
        self._start_verifications()
        self._report_stuck()

    # -- verification of claims ------------------------------------------------
    def _start_verifications(self) -> None:
        """Verify `review` tasks once their worker process is gone (worktree quiescent)."""
        try:
            pending = self.board.reviewable(self.feature)
        except TASK_ERRORS as e:
            _warn(f"board read: {e}")
            return
        live = {(r.feature, r.project) for r in self.running.values()}
        timeout = float(self.aos.settings["board"].get("verify_timeout_sec", 600))
        for t in pending:
            if t["review_stage"] is not None:  # already verified; the reviewer takes it from here
                continue
            if t["id"] in self.running or t["id"] in self.verifying or (t["feature"], t["project"]) in live:
                continue
            worker = ((self.aos.projects().get(t["project"]) or {}).get("worker")) or {}
            wt = worktree_path(self.aos.home, t["feature"], t["project"])
            self.verifying[t["id"]] = self._pool.submit(verify_claim, t, wt, worker.get("verify_command"), timeout)

    def _collect_verifications(self) -> None:
        for tid, fut in list(self.verifying.items()):
            if not fut.done():
                continue
            del self.verifying[tid]
            try:
                ok, detail = fut.result()
            except Exception as e:  # a broken check is a rejected claim, never a crashed loop
                ok, detail = False, f"verification error: {type(e).__name__}: {e}"
            try:
                if ok and self.aos.settings["board"].get("review", True):
                    self.board.mark_verified(tid, detail)  # an independent reviewer is next
                elif ok:
                    self.board.accept(tid, detail)
                else:
                    self.board.reject_review(tid, detail)
            except TASK_ERRORS as e:
                _warn(f"task #{tid}: {e}")

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
        if r.acp:
            r.acp.session.cancel()
            r.acp.close()
        _kill_group(r.proc.pid, proc=r.proc)

    # -- live ACP sessions -------------------------------------------------------
    def _service_acp(self) -> None:
        """Drain each live session's events on this thread; close a session once its turn
        is over (the process then exits and is reaped like any other)."""
        for tid, r in list(self.running.items()):
            if not r.acp or r.acp.closed:
                continue
            try:
                r.acp.session.drain(on_request=lambda rid, m, p, tid=tid, r=r: self._on_agent_request(tid, r, rid, m, p))
                if r.acp.turn_finished():
                    r.acp.close()
            except TASK_ERRORS as e:
                _warn(f"task #{tid}: {e}")

    def _on_agent_request(self, tid: int, r: Running, req_id, method: str, params: dict) -> None:
        conn = r.acp.conn
        if method != "session/request_permission":
            conn.respond(req_id, error={"code": -32601, "message": f"aos does not implement {method}"})
            return
        verdict = decide(params.get("toolCall") or {}, r.allowed or [], r.worktree or Path("."))
        options = params.get("options") or []
        if verdict.decision == "ask":  # the user decides; the worker waits (not a stall)
            pid = self.board.request_permission(tid, verdict.summary, json.dumps(params.get("toolCall") or {})[:4000],
                                                verdict.rule, generation=r.generation, role=r.kind)
            self.pending_perms[pid] = (tid, r, req_id, options, verdict.rule, time.monotonic())
            return
        if verdict.decision == "reject":
            self.board.log_event(tid, "status", f"permission refused: {verdict.summary}")
        self._answer_permission(r, req_id, options, verdict.decision)

    @staticmethod
    def _answer_permission(r: Running, req_id, options: list, decision: str) -> None:
        choice = option_for(decision, options)
        try:
            r.acp.conn.respond(req_id, {"outcome": {"outcome": "selected", "optionId": choice}} if choice
                               else {"outcome": {"outcome": "cancelled"}})
        except Exception as e:  # the agent may be gone already
            _warn(f"permission answer not delivered: {e}")

    def _resolve_permissions(self) -> None:
        """Answer waiting permission requests the user decided; refuse ones nobody answered."""
        timeout = 60 * float(self.aos.settings["board"].get("approval_timeout_min", 30))
        for pid, (tid, r, req_id, options, rule, asked) in list(self.pending_perms.items()):
            try:
                p = self.board.permission(pid)
                live = self.running.get(tid) is r and r.acp and not r.acp.closed
                if not live:
                    self.board.expire_permission(pid, "the worker is gone")
                elif p["status"] == "approved":
                    if p["always"] and rule:
                        aos = AOS(self.aos.repo, home=self.aos.home, data=self.aos.data)
                        add_allowed_tool(aos, r.project, rule)
                        r.allowed = [*(r.allowed or []), rule]
                    self._answer_permission(r, req_id, options, "always" if p["always"] else "allow")
                elif p["status"] in ("denied", "expired"):
                    self._answer_permission(r, req_id, options, "reject")
                elif time.monotonic() - asked > timeout:
                    self.board.expire_permission(pid, "no answer in time")
                    self._answer_permission(r, req_id, options, "reject")
                else:
                    continue
            except TASK_ERRORS as e:
                _warn(f"permission #{pid}: {e}")
            del self.pending_perms[pid]

    def _spawn(self, tid: int, spec, settings: dict, mod, log_path: Path):
        """Start a worker/reviewer: a live ACP session when the profile's transport is acp
        (falling back to the CLI if ACP is unavailable here), else a one-shot process.
        Returns (proc, launch, acp, log, transport)."""
        tool = settings.get("adapter")
        if settings.get("transport") == "acp" and tool in ("claude", "kiro"):
            launch = mod.prepare(spec, settings) if tool == "kiro" else Launch([], spec.worktree, {})
            acp = AcpRun(tool, settings, self.aos.home, log_path)
            try:
                from .workers import kiro as kiro_mod
                acp.start(spec, kiro_agent=kiro_mod.agent_name(spec.task) if tool == "kiro" else None,
                          resume_id=spec.session_id if spec.resume else None)
                return acp.proc, launch, acp, None, "acp"
            except AdapterMissing as e:
                launch.cleanup()
                self.board.log_event(tid, "status", f"ACP unavailable ({e.message}); using the CLI for this attempt")
            except Exception:
                launch.cleanup()
                raise
        launch = mod.prepare(spec, settings)
        log = open(log_path, "ab")
        try:
            proc = subprocess.Popen(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except Exception:
            log.close()
            launch.cleanup()
            raise
        return proc, launch, None, log, "cli"

    def _reap(self) -> None:
        for tid, r in list(self.running.items()):
            try:
                self._reap_one(tid, r)
            except TASK_ERRORS as e:
                _warn(f"task #{tid}: {e}")

    def _still_owns(self, r: Running, task: dict) -> bool:
        """Is the board still waiting on this process? A worker owns a `running` task; a
        reviewer owns a task in review stage `reviewing` under its own generation."""
        if r.kind == "reviewer":
            return (task["status"] == "review" and task["review_stage"] == "reviewing"
                    and task["generation"] == r.generation)
        return task["status"] == "running"

    def _reap_one(self, tid: int, r: Running) -> None:
        code = r.proc.poll()
        if code is None:
            task = self.board.task(tid)
            now = time.monotonic()
            if task["status"] == "cancelled":
                self._terminate(r)
            elif now > r.deadline:
                r.timed_out = True
                self._terminate(r)
            elif not self._still_owns(r, task):
                # completed/blocked but still alive: give it a moment to exit, then stop it
                r.finished_at = r.finished_at or now
                if now - r.finished_at >= self.grace_s:
                    self._terminate(r)
            code = r.proc.poll()
            if code is None:
                return
        # the leader exited; stop anything it left behind in its process group
        _kill_group(r.proc.pid, wait=1.0, proc=r.proc)
        if r.acp:
            r.acp.close()
        r.launch.cleanup()
        if r.log:
            r.log.close()
        del self.running[tid]
        self._record_cost(tid, r)
        task = self.board.task(tid)
        if not self._still_owns(r, task):
            return
        if r.kind == "reviewer":
            why = (f"timed out after {r.timeout_min:g} min" if r.timed_out
                   else f"exited with code {code}" if code else "exited without a verdict")
            self.board.review_unfinished(tid, why)
            return
        if r.timed_out:
            why = f"timed out after {r.timeout_min:g} min"
        elif code in r.exit_reasons:  # the tool says it never got going (e.g. MCP startup)
            self.board.attempt_failed(tid, r.exit_reasons[code], ran=False)
            return
        elif code == 0:
            why = "exited without task_complete"
        else:
            why = f"exited with code {code}"
        self.board.attempt_failed(tid, why)

    def _record_cost(self, tid: int, r: Running) -> None:
        if r.acp:
            if r.acp.session and r.acp.session.cost > 0:
                self.board.add_cost(tid, r.acp.session.cost, role=r.kind, generation=r.generation,
                                    unit=r.acp.session.cost_unit)
            return
        if not r.log_path:
            return
        try:
            with open(r.log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - 262_144))
                usd = parse_cost(fh.read().decode("utf-8", errors="replace"))
        except OSError:
            return
        if usd is not None:
            self.board.add_cost(tid, usd, role=r.kind, generation=r.generation)

    def _over_budget(self, t: dict) -> str | None:
        cfg = self.aos.settings["board"]
        for unit, suffix, fmt in (("USD", "usd", "${:.2f}"), ("credit", "credits", "{:.2f} credits")):
            task_budget, feature_budget = cfg.get(f"task_budget_{suffix}"), cfg.get(f"feature_budget_{suffix}")
            if task_budget is not None:
                spent = self.board.task_cost(t["id"], unit)
                if spent >= float(task_budget):
                    return (f"task budget reached: {fmt.format(spent)} of {fmt.format(float(task_budget))} spent; "
                            f"raise board.task_budget_{suffix} in aos.yaml and unblock, or cancel the task")
            if feature_budget is not None:
                spent = self.board.feature_cost(t["feature"], unit)
                if spent >= float(feature_budget):
                    return (f"feature budget reached: {fmt.format(spent)} of {fmt.format(float(feature_budget))} "
                            f"spent on {t['feature']}; raise board.feature_budget_{suffix} in aos.yaml and unblock")
        return None

    def _launch_ready(self) -> None:
        try:
            busy = {(t["feature"], t["project"]) for t in self.board.tasks(status="running")}
            ready = self.board.dispatchable(self.feature)
        except TASK_ERRORS as e:
            _warn(f"board read: {e}")
            return
        busy |= {(r.feature, r.project) for r in self.running.values()}
        # a worktree whose last task awaits verification is not free yet
        try:
            busy |= {(t["feature"], t["project"]) for t in self.board.reviewable()}
        except TASK_ERRORS as e:
            _warn(f"board read: {e}")
            return
        for t in ready:
            key = (t["feature"], t["project"])
            if t["id"] in self.running or key in busy:
                continue
            if not self._may_start():
                break
            busy.add(key)
            try:
                self._launch(t)
            except TASK_ERRORS as e:
                _warn(f"task #{t['id']}: {e}")

    def _may_start(self) -> bool:
        """One more worker? Under the ceiling, and enough memory left after it. Workers started
        in the last minute haven't used their memory yet, so their estimate is reserved. With
        nothing running one always starts, so work can't freeze."""
        n = len(self.running)
        if n >= self.parallel:
            return False
        if time.monotonic() - self._last_start < self.stagger_s:
            return False  # stagger cold starts: each one spawns its MCP servers too
        if n == 0:
            return True
        free = available_gb()
        if free is None:
            return True
        now = time.monotonic()
        fresh = sum(1 for r in self.running.values() if now - r.started_at < self.settle_s)
        projected = free - self.worker_gb * (fresh + 1)
        if projected < self.min_free_gb:
            reason = (f"holding new workers: {free:.1f} GB free, {projected:.1f} GB would remain "
                      f"(minimum {self.min_free_gb:g} GB)")
            if reason != self._held_reason:
                _warn(reason)
                self._held_reason = reason
            return False
        self._held_reason = ""
        return True

    def _launch_reviews(self) -> None:
        """Start an independent reviewer for each verified claim whose worktree is free."""
        try:
            pending = [t for t in self.board.reviewable(self.feature) if t["review_stage"] == "verified"]
        except TASK_ERRORS as e:
            _warn(f"board read: {e}")
            return
        live = {(r.feature, r.project) for r in self.running.values()}
        for t in pending:
            if t["id"] in self.running or (t["feature"], t["project"]) in live:
                continue
            if not self._may_start():
                break
            live.add((t["feature"], t["project"]))
            try:
                self._launch_review(t)
            except TASK_ERRORS as e:
                _warn(f"task #{t['id']} review: {e}")

    def _launch_review(self, t: dict) -> None:
        tid = t["id"]
        task = self.board.start_review(tid)  # new generation: the reviewer's own token
        launch = None
        try:
            project_path = linked_project_path(t["project"])
            wt = worktree_path(self.aos.home, t["feature"], t["project"])
            aos = AOS(self.aos.repo, home=self.aos.home, slug=t["project"], data=self.aos.data)
            spec, settings = prepare_review(aos, self.board, task, wt, project_path, self.aos_bin)
            mod = adapter(settings["adapter"])
            log_path = self.board.data / "logs" / f"{tid}-review-{task['review_attempts']}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            proc, launch, acp, log, transport = self._spawn(tid, spec, settings, mod, log_path)
        except Exception as e:  # a reviewer that cannot start counts as a review without verdict
            if launch:
                launch.cleanup()
            self.board.review_unfinished(tid, f"reviewer launch failed: {e}")
            return
        timeout = float(self.aos.settings["board"].get("review_timeout_min", 15))
        self.running[tid] = Running(proc, launch, time.monotonic() + 60 * timeout, timeout, log,
                                    getattr(mod, "EXIT_REASONS", {}), t["feature"], t["project"],
                                    kind="reviewer", generation=task["generation"], started_at=time.monotonic(),
                                    log_path=log_path, acp=acp, allowed=spec.allowed, worktree=spec.worktree)
        self._last_start = time.monotonic()

    def _launch(self, t: dict) -> None:
        tid = t["id"]
        if self.worker and t["worker"] != self.worker:
            self.board.set_worker(tid, self.worker, why=" for this run (aos board run --worker)")
            t = {**t, "worker": self.worker}
        over = self._over_budget(t)
        if over:
            self.board.block(tid, over, author="dispatcher")
            return
        try:
            project_path = linked_project_path(t["project"])
            wt = ensure_worktree(project_path, worktree_path(self.aos.home, t["feature"], t["project"]),
                                 branch_name(t["feature"]))
        except AosError as e:
            self.board.block(tid, e.message + (f" ({e.hint})" if e.hint else ""), author="dispatcher")
            return
        if t.get("revert_to"):  # the previous attempt was rejected: start from its base again
            ok, detail = revert_to(wt, t["revert_to"])
            self.board.log_event(tid, "reverted" if ok else "status", detail)
        task = self.board.claim(tid, base_commit=head_commit(wt))  # raises if the task moved meanwhile
        launch = None
        try:
            aos = AOS(self.aos.repo, home=self.aos.home, slug=t["project"], data=self.aos.data)
            spec, settings = prepare_run(aos, self.board, task, wt, project_path, self.aos_bin)
            mod = adapter(settings["adapter"])
            log_path = self.board.data / "logs" / f"{tid}-{task['attempts']}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            proc, launch, acp, log, transport = self._spawn(tid, spec, settings, mod, log_path)
        except Exception as e:  # any launch failure is a failed attempt, never a crash of the loop
            if launch:
                launch.cleanup()
            self.board.attempt_failed(tid, f"launch failed: {e}", ran=False)
            return
        timeout = float(settings["timeout_min"])
        self.running[tid] = Running(proc, launch, time.monotonic() + 60 * timeout, timeout, log,
                                    getattr(mod, "EXIT_REASONS", {}), t["feature"], t["project"],
                                    generation=task["generation"], started_at=time.monotonic(),
                                    log_path=log_path, acp=acp, allowed=spec.allowed, worktree=spec.worktree)
        self._last_start = time.monotonic()
        self.board.set_transport(tid, transport)
        session_id = acp.session.session_id if acp else launch.session_id
        self.board.set_process(tid, proc.pid, session_id, proc_start=process_start(proc.pid))
