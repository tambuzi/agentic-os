"""Spike: learning from real Claude Code sessions, end to end (session learning, plan Task 5).

Throwaway. In a throwaway AOS_HOME with one linked demo project, it drives two real Claude Code
sessions headless (`claude -p`, then `--resume` for the user's correction). Real hooks run:
SessionEnd → `aos lessons capture` → background learner. Checks:
  1. after session 1: a queue entry, then a session lesson at 0.4, NOT in `aos context`
  2. after session 2 (same correction): the lesson at >= 0.5 and in `aos context`

  ~/.agenticos/venv/bin/python spikes/session-learning-e2e/run.py
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
AOS = [shutil.which("aos") or "aos"]


class Run:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="aos-session-e2e-", dir=Path.home()))
        self.home, self.demo = self.root / "home", self.root / "demo"
        self.env = {**os.environ, "AOS_HOME": str(self.home)}
        self.notes, self.checks = [], []

    def sh(self, argv, cwd=None, timeout=600):
        r = subprocess.run(argv, env=self.env, cwd=cwd or self.root, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr

    def note(self, m):
        line = f"{time.strftime('%H:%M:%S')} {m}"
        self.notes.append(line)
        print(line, flush=True)

    def check(self, ok, what):
        self.checks.append((bool(ok), what))
        self.note(("ok   " if ok else "FAIL ") + what)

    def setup(self):
        self.sh([*AOS, "init", str(REPO)])
        self.sh([*AOS, "worker", "claude"])
        (self.home / "acp").symlink_to(Path.home() / ".agenticos" / "acp")
        self.demo.mkdir()
        (self.demo / "README.md").write_text("# demo\n")
        for a in (["init", "-q"], ["config", "core.hooksPath", "/dev/null"], ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(["git", *a], cwd=self.demo, check=True, capture_output=True)
        code, out, err = self.sh([*AOS, "link", str(self.demo), "--name", "demo", "--no-graphskill"])
        hooks = json.loads((self.demo / ".claude" / "settings.local.json").read_text())["hooks"]
        self.check("SessionEnd" in hooks, "aos link installed the SessionEnd hook")

    def session(self, create: str, correction: str) -> None:
        base = ["claude", "-p", "--output-format", "json", "--permission-mode", "acceptEdits", "--model", "haiku"]
        code, out, err = self.sh([*base, create], cwd=self.demo)
        sid = json.loads(out)["session_id"]
        self.note(f"session {sid}: created ({code})")
        code, out, err = self.sh([*base, "--resume", sid, correction], cwd=self.demo)
        self.note(f"session {sid}: corrected ({code})")
        return sid

    def wait_done(self, n_done: int, timeout=240):
        from aos import sessions
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            done = [e for e in sessions.entries(self.home / "data") if e["status"] in ("done", "failed")]
            if len(done) >= n_done:
                return done
            time.sleep(3)
        return [e for e in sessions.entries(self.home / "data") if e["status"] in ("done", "failed")]

    def lessons(self):
        from aos.lessons import LessonStore
        return LessonStore(self.home / "data", "demo").list()[0]

    def context(self):
        return self.sh([*AOS, "context", "--project", str(self.demo)])[1]

    def drive(self):
        os.environ["AOS_HOME"] = str(self.home)  # for the in-process aos imports below
        self.session("Create a Python module named MyHelper.py with a function hello() that returns 'hi'.",
                     "No, use snake_case for module names in this project: create my_helper.py with that code "
                     "instead (leave the other file).")
        done = self.wait_done(1)
        from aos import sessions
        self.note("queue: " + json.dumps([{k: e.get(k) for k in ("id", "status", "tries", "reason")}
                                          for e in sessions.entries(self.home / "data")]))
        self.check(bool(done) and done[0]["status"] == "done", "session 1 queued and learned from")
        ls = self.lessons()
        self.note("lessons: " + json.dumps([{"trigger": l.trigger, "action": l.action, "conf": l.confidence} for l in ls]))
        self.check(any(l.confidence == 0.4 for l in ls), "a session lesson at 0.4")
        self.check("## Lessons for demo" not in self.context(), "not in context yet")

        self.session("Create a Python module named DataLoader.py with a function load() that returns [].",
                     "No, module names are snake_case here: create data_loader.py with that code instead.")
        self.wait_done(2)
        ls = self.lessons()
        self.note("lessons: " + json.dumps([{"trigger": l.trigger, "action": l.action, "conf": l.confidence} for l in ls]))
        self.check(any(l.confidence >= 0.5 for l in ls), "seen again: a lesson at >= 0.5")
        self.check("## Lessons for demo" in self.context(), "now in context")

    def report(self):
        ok = all(c for c, _ in self.checks)
        lines = ["# Session learning end-to-end (claude)", "", f"- date: {datetime.datetime.now():%Y-%m-%dT%H:%M}",
                 f"- result: **{'PASS' if ok else 'FAIL'}** ({sum(c for c, _ in self.checks)}/{len(self.checks)})", "",
                 "## Checks", ""] + [f"- {'✅' if c else '❌'} {w}" for c, w in self.checks] + \
                ["", "## Driver", "", "```", *self.notes, "```"]
        log = self.home / "data" / "logs" / "sessions.log"
        if log.exists():
            lines += ["", "## sessions.log (tail)", "", "```", *log.read_text().splitlines()[-30:], "```"]
        out = Path.cwd() / "session-e2e-report.md"
        out.write_text("\n".join(lines) + "\n")
        print(f"report: {out}")
        return ok


if __name__ == "__main__":
    r = Run()
    try:
        r.setup()
        r.drive()
    finally:
        ok = r.report()
        folder = Path.home() / ".claude" / "projects" / __import__("aos.sessions", fromlist=["x"]).claude_project_folder(r.demo)
        shutil.rmtree(folder, ignore_errors=True)       # the demo's transcripts in Claude's own store
        shutil.rmtree(r.root, ignore_errors=True)
    sys.exit(0 if ok else 1)
