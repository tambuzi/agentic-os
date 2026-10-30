"""The background learner for one queued session (spec 2026-10-30, learning loop phase B).

Runs outside the board dispatcher: claims a queue entry, keeps the daily budget, runs a short
read-only learner (live ACP session, or the tool's CLI when ACP isn't available) with the
session learner tools only, records its cost and settles the entry.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from . import sessions
from .core import AOS
from .store import write_atomic

SESSION_NOTE = """\
You are learning from one of the user's own Claude Code / Kiro sessions on this project, not
from a board task. The corrections are excerpts of what the user said to their agent: the user
corrected it, refused a tool call, or the agent fixed its own failed command. Turn the project
fact behind each correction into a lesson (or confirm an existing one); "nothing reusable" is a
valid answer when the user simply changed their mind."""


def _aos_command() -> list[str]:
    exe = shutil.which("aos")
    return [exe] if exe else [sys.executable, "-m", "aos"]


def _costs_file(data) -> Path:
    return Path(data) / "sessions" / "costs.jsonl"


def spent_today(data, unit: str = "USD") -> float:
    p = _costs_file(data)
    if not p.exists():
        return 0.0
    today, total = date.today().isoformat(), 0.0
    for line in p.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("at", "")[:10] == today and row.get("unit", "USD") == unit:
            total += float(row.get("amount") or 0)
    return total


def _record_cost(data, entry_id: str, amount: float, unit: str) -> None:
    if amount <= 0:
        return
    p = _costs_file(data)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps({"entry": entry_id, "amount": round(amount, 6), "unit": unit,
                             "at": date.today().isoformat()}) + "\n")


def _spec(aos: AOS, entry: dict, settings: dict):
    from .context import build_context
    from .workers.common import LEARNER_PROTOCOL, RunSpec
    run_dir = Path(aos.data) / "runs" / f"session-{entry['id']}"
    run_dir.mkdir(parents=True, exist_ok=True)
    ctx = AOS(aos.repo, home=aos.home, slug=entry["project"], data=aos.data)
    context_file = run_dir / "context.md"
    write_atomic(context_file, "\n\n".join([build_context(ctx).rstrip(), "## Learner protocol\n\n" + LEARNER_PROTOCOL,
                                            "## This run\n\n" + SESSION_NOTE]) + "\n")
    cmd = _aos_command()
    servers = {"aos": {"command": cmd[0], "args": [*cmd[1:], "serve", "--project", entry["project_dir"],
                                                   "--learn-session", entry["id"]]}}
    task = {"id": 0, "feature": "session", "project": entry["project"], "title": "learn from a session",
            "spec": "", "worker": entry["tool"]}
    prompt = (f"Learn from the user's {entry['tool']} session on {entry['project']}. Call learning_input, "
              "then follow the learner protocol and finish with learning_done.")
    return RunSpec(task=task, project_path=Path(entry["project_dir"]), worktree=run_dir, run_dir=run_dir,
                   context_file=context_file, prompt=prompt, mcp_servers=servers, allowed=["mcp:aos"],
                   model=settings.get("model"), resume=False, session_id=None, role="learner")


def _run_acp(aos: AOS, tool: str, settings: dict, spec, log_path: Path, timeout_s: float) -> tuple[float, str, str]:
    """(cost, unit, problem). Raises tools.AdapterMissing when ACP isn't available here."""
    from .acp.permissions import decide
    from .acp.runner import AcpRun
    from .acp.tools import option_for
    run = AcpRun(tool, settings, aos.home, log_path)
    run.start(spec)
    conn = run.conn

    def on_request(req_id, method, params):  # lesson tools only: anything else is refused
        if method != "session/request_permission":
            conn.respond(req_id, error={"code": -32601, "message": f"aos does not implement {method}"})
            return
        ok = decide(params.get("toolCall") or {}, ["mcp:aos"], spec.worktree).decision == "allow"
        choice = option_for("allow" if ok else "reject", params.get("options") or [])
        conn.respond(req_id, {"outcome": {"outcome": "selected", "optionId": choice}} if choice
                     else {"outcome": {"outcome": "cancelled"}})

    deadline, problem = time.monotonic() + timeout_s, ""
    try:
        while True:
            run.session.drain(on_request=on_request)
            if run.turn_finished():
                stop, error = run.session.turn_outcome()
                problem = f"turn failed: {error}" if error else ""
                break
            if not conn.alive():
                problem = f"the agent exited (code {conn.proc.poll()})"
                break
            if time.monotonic() > deadline:
                run.session.cancel()
                problem = f"timed out after {timeout_s / 60:g} min"
                break
            time.sleep(0.1)
        run.session.drain(on_request=lambda *_: None)
        return run.session.cost, run.session.cost_unit, problem
    finally:
        run.close()


def _run_cli(aos: AOS, settings: dict, spec, log_path: Path, timeout_s: float) -> str:
    from .workers import adapter
    mod = adapter(settings["adapter"])
    launch = mod.prepare(spec, settings)
    try:
        with open(log_path, "ab") as log:
            subprocess.run(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s)
        return ""
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout_s / 60:g} min"
    finally:
        launch.cleanup()


def learn_entry(aos: AOS, entry_id: str) -> str:
    """Process one queued session. Returns "<outcome>: <detail>"; never raises."""
    data = aos.data
    e = sessions.claim(data, entry_id)
    if not e:
        return "skipped: not pending (taken by another job, done, or unknown)"
    try:
        learning = aos.settings.get("learning") or {}
        budget = float(learning.get("sessions_daily_budget_usd") or 0)
        if budget and spent_today(data) >= budget:
            sessions.release(data, entry_id, "daily budget reached; waits for tomorrow")
            return f"deferred: daily budget of ${budget:.2f} reached"
        if e["tool"] != "claude":
            sessions.finish(data, entry_id, ok=False, reason=f"{e['tool']} sessions can't be learned from yet")
            return "failed: unsupported tool"
        from .acp.tools import AdapterMissing
        from .workers.common import profile_settings
        settings = profile_settings(AOS(aos.repo, home=aos.home, slug=e["project"], data=data), e["tool"], e["project"])
        settings = {**settings, "allowed_tools": ["mcp:aos"],
                    "model": settings.get("learning_model") or settings.get("model")}
        spec = _spec(aos, e, settings)
        log_path = Path(data) / "logs" / f"session-{entry_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timeout_s = 60 * float(learning.get("learner_timeout_min", 5))
        try:
            cost, unit, problem = _run_acp(aos, e["tool"], settings, spec, log_path, timeout_s)
            _record_cost(data, entry_id, cost, unit)
        except AdapterMissing:
            problem = _run_cli(aos, settings, spec, log_path, timeout_s)
        after = sessions.entry(data, entry_id) or {}
        if after.get("status") == "done":
            return f"done: {after.get('reason') or 'learned'}"
        sessions.finish(data, entry_id, ok=False, reason=problem or "ended without learning_done")
        return f"failed: {problem or 'ended without learning_done'}"
    except Exception as exc:  # a background job: record why, never crash silently
        sessions.finish(data, entry_id, ok=False, reason=f"{type(exc).__name__}: {exc}")
        return f"failed: {exc}"


def learn_pending(aos: AOS, limit: int = 5) -> list[str]:
    sessions.prune(aos.data)
    return [learn_entry(aos, e["id"]) for e in sessions.entries(aos.data, "pending")[:limit]]


def learn_last(aos: AOS, project_dir, tool: str = "claude") -> str:
    """Manual trigger: learn from the project's most recent session even without corrections."""
    from .config import slug_for_path
    pdir = Path(project_dir).expanduser().resolve()
    slug = slug_for_path(pdir)
    if not slug:
        return f"failed: {pdir} is not a linked project"
    if tool != "claude":
        return f"failed: {tool} capture not available yet"
    folder = Path.home() / ".claude" / "projects" / sessions.claude_project_folder(pdir)
    transcripts = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    if not transcripts:
        return f"failed: no Claude Code session found for {pdir}"
    newest = transcripts[-1]
    key = re.sub(r"\W", "", f"manual{newest.stem}{int(newest.stat().st_mtime)}")[:32]
    entry = sessions.enqueue(aos.data, tool, newest.stem, slug, pdir, newest, [{"key": key, "kind": "manual"}],
                             all_turns=True)
    if not entry:
        return "skipped: this session was already learned from"
    return learn_entry(aos, entry["id"])
