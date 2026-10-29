"""Spike: the learning loop end to end with a real agent tool (Claude or Kiro).

Throwaway verification, not product code. In a throwaway AOS_HOME (real business data is never
touched) with two demo projects, api and web. The team convention "every new function gets a
CHANGELOG.md entry" is never written in a spec; the user gives it as a mid-task steer:

  1. feature f1 on api: the driver steers the worker with the convention, then a learner run
     records a lesson                                            -> lesson on api with evidence
  2. feature f2 on api, no steer: the lesson is in the new worker's context, and the worker
     follows it on its own                                       -> CHANGELOG.md mentions sub
  3. `aos lessons confirm` on the api lesson                      -> confirmed, 0.8
  4. feature f3 on web: the same correction; the learner sees the api lesson in other_projects
     and links it (same_as + general_rule)                        -> web lesson linked (AI-dependent)
  5. `aos lessons confirm` on the web lesson                      -> one org inbox proposal
  6. `aos inbox apply`                                            -> the rule is in org memory

  ~/.agenticos/venv/bin/python spikes/learning-e2e/run.py --tool claude
  ~/.agenticos/venv/bin/python spikes/learning-e2e/run.py --tool kiro
Options: --keep, --timeout MIN per feature (default 20), --report PATH.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
AOS = [sys.executable, "-c", "import sys; from aos.cli import main; sys.exit(main(sys.argv[1:]))"]
VERIFY = "python3 test_calc.py"
STEER = "Team convention: every new function gets a one-line entry in CHANGELOG.md, in the same commit."
SPEC = ("Add a function `{fn}(a, b)` to calc.py that returns {expr}, and add an assertion for it to "
        "test_calc.py. Run `python3 test_calc.py` to check it passes, then commit your change with git.")


class Run:
    def __init__(self, args):
        self.args = args
        self.root = Path(tempfile.mkdtemp(prefix="aos-learning-e2e-", dir=Path.home()))
        self.home = self.root / "home"
        self.env = {**os.environ, "AOS_HOME": str(self.home), "PYTHONPATH": str(REPO)}
        self.notes: list[str] = []
        self.checks: list[tuple[bool, str]] = []
        self.out: dict[str, str] = {}

    # -- helpers ---------------------------------------------------------------
    def aos(self, *argv, check=True) -> str:
        r = subprocess.run([*AOS, *argv], env=self.env, capture_output=True, text=True, cwd=self.root)
        text = (r.stdout + r.stderr).strip()
        if check and r.returncode != 0:
            raise SystemExit(f"`aos {' '.join(argv)}` failed:\n{text}")
        return text

    def note(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.notes.append(f"{stamp} {msg}")
        print(f"[{stamp}] {msg}", flush=True)

    def check(self, ok: bool, what: str) -> None:
        self.checks.append((bool(ok), what))
        self.note(("ok   " if ok else "FAIL ") + what)

    def board(self):
        from aos.board import Board
        return Board(self.home / "data")

    def lessons(self, project: str):
        from aos.lessons import LessonStore
        return LessonStore(self.home / "data", project).list()[0]

    # -- setup -----------------------------------------------------------------
    def project(self, name: str) -> Path:
        d = self.root / name
        d.mkdir()
        (d / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (d / "test_calc.py").write_text('from calc import *\nassert add(2, 3) == 5\nprint("ok")\n')
        (d / "CHANGELOG.md").write_text("# Changelog\n\n- add(a, b)\n")
        git = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
        git("init", "-q")
        git("config", "core.hooksPath", "/dev/null")  # global hooks would block the workers' commits
        git("add", ".")
        git("commit", "-qm", "init")
        self.aos("link", str(d), "--name", name, "--no-graphskill")
        git("add", "-A")
        git("commit", "-qm", "aos link")
        return d

    def setup(self) -> None:
        if not shutil.which("kiro-cli" if self.args.tool == "kiro" else "claude"):
            raise SystemExit(f"{self.args.tool} not on PATH")
        self.aos("init", str(REPO))
        self.aos("worker", self.args.tool)
        for name in ("api", "web"):
            self.project(name)
        import yaml
        p = self.home / "data" / "projects.yaml"
        d = yaml.safe_load(p.read_text())
        for name in ("api", "web"):
            d["projects"][name]["worker"] = {}
        p.write_text(yaml.safe_dump(d))
        adapter = Path.home() / ".agenticos" / "acp"
        if self.args.tool == "claude" and adapter.is_dir():  # reuse the installed Claude ACP adapter
            (self.home / "acp").symlink_to(adapter)
        self.out["doctor"] = self.aos("doctor", check=False)
        self.check(f"{self.args.tool}: acp" in self.out["doctor"], f"doctor: {self.args.tool} runs over ACP")
        self.note(f"test home {self.home}")

    def set_verify(self, project: str, fn: str) -> None:
        import yaml
        p = self.home / "data" / "projects.yaml"
        d = yaml.safe_load(p.read_text())
        d["projects"][project]["worker"]["verify_command"] = VERIFY.format(fn=fn)
        p.write_text(yaml.safe_dump(d))

    # -- one feature -----------------------------------------------------------
    def feature(self, slug: str, project: str, fn: str, expr: str, steer: bool = False) -> int:
        self.set_verify(project, fn)
        spec = self.root / f"{slug}.md"
        spec.write_text(SPEC.format(fn=fn, expr=expr))
        self.aos("feature", "new", slug, "--title", f"{slug}: {fn}", "--projects", project)
        out = self.aos("task", "add", slug, project, f"Add {fn}", "--spec-file", str(spec))
        tid = int(out.split("#")[1].split()[0])
        log = open(self.root / f"dispatcher-{slug}.log", "w")
        disp = subprocess.Popen([*AOS, "board", "run", "--feature", slug, "--until-done"], env=self.env,
                                cwd=self.root, stdout=log, stderr=subprocess.STDOUT)
        self.note(f"{slug}: task #{tid} on {project}, dispatcher pid {disp.pid}")
        b, deadline = self.board(), time.monotonic() + 60 * self.args.timeout
        steered = not steer
        try:
            while disp.poll() is None and time.monotonic() < deadline:
                if not steered and b.task(tid)["status"] == "running":
                    self.note(f"{slug}: steer #{tid}: {STEER}")
                    self.aos("task", "steer", str(tid), STEER)
                    steered = True
                for p in b.permissions(status="pending"):
                    self.note(f"approve permission #{p['id']} ({p['role']}): {p['summary'][:90]}")
                    self.aos("task", "approve", str(p["id"]), check=False)
                time.sleep(2)
        finally:
            if disp.poll() is None:
                self.note(f"{slug}: timeout, stopping the dispatcher")
                disp.send_signal(2)
                time.sleep(3)
                disp.send_signal(2)
                disp.wait(30)
            log.close()
        t = b.task(tid)
        events = b.task_events(tid)
        self.out[f"{slug} timeline"] = "\n".join(f"{e['id']:>3} {e['kind']:<14} {(e['body'] or '')[:150]}"
                                                 for e in events)
        self.note(f"{slug}: task {t['status']} after {t['attempts']} attempt(s); "
                  f"learned: {[e['body'] for e in events if e['kind'] == 'learned']}")
        return tid

    # -- scenario --------------------------------------------------------------
    def drive(self) -> None:
        t1 = self.feature("f1", "api", "mul", "a * b", steer=True)
        b = self.board()
        self.check(b.task(t1)["status"] == "done", "f1: task done")
        self.check(any(s["kind"] == "steer" for s in b.correction_signals(t1)), "f1: the steer is a correction signal")
        self.check(any(e["kind"] == "learned" for e in b.task_events(t1)), "f1: a learner ran")
        api = [l for l in self.lessons("api") if l.status != "rejected"]
        self.check(bool(api), f"f1: lesson(s) on api: {[l.trigger + ' -> ' + l.action for l in api]}")
        self.check(any("changelog" in (l.trigger + l.action).lower() for l in api),
                   "f1: the lesson states the project fact (CHANGELOG), not generic advice")
        if not api:
            return
        lesson = max(api, key=lambda l: l.confidence)

        t2 = self.feature("f2", "api", "sub", "a - b")
        ctx = (self.home / "data" / "runs" / f"{t2}-1" / "context.md")
        self.check(ctx.exists() and lesson.trigger in ctx.read_text(), "f2: the lesson is in the worker's context")
        wt = self.home / "worktrees" / "f2" / "api"
        changelog = (wt / "CHANGELOG.md").read_text() if (wt / "CHANGELOG.md").exists() else ""
        self.check("sub" in changelog, "f2: without a steer, the worker followed the lesson (CHANGELOG mentions sub)")

        self.out["confirm api"] = self.aos("lessons", "confirm", lesson.ref)
        from aos.lessons import find
        self.check(find(self.home / "data", lesson.ref).confidence == 0.8, "confirm: api lesson at 0.8")

        self.feature("f3", "web", "mul", "a * b", steer=True)
        web = [l for l in self.lessons("web") if l.status != "rejected"]
        self.check(bool(web), f"f3: lesson(s) on web: {[l.trigger for l in web]}")
        linked = [l for l in web if lesson.ref in l.same_as]
        self.check(bool(linked), "f3: the web lesson links the api lesson (same_as + general_rule)")
        if not linked:
            return
        out = self.aos("lessons", "confirm", linked[0].ref)
        self.out["confirm web"] = out
        pids = [line.split("aos inbox show ")[1].split()[0] for line in out.splitlines() if "promotion proposed" in line]
        self.check(len(pids) == 1, "confirm: one org promotion proposed")
        if pids:
            self.out["inbox apply"] = self.aos("inbox", "apply", pids[0])
            from aos.core import AOS as Core
            org = Core(REPO, home=self.home, data=self.home / "data").memory("org").entries()
            self.out["org memory"] = "\n§\n".join(org)
            self.check(any((linked[0].general_rule or "")[:30] in e for e in org), "apply: the rule is in org memory")

    def report(self) -> Path:
        ok = all(c for c, _ in self.checks)
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        lines = [f"# Learning loop end-to-end ({self.args.tool})", "",
                 f"- date: {datetime.datetime.now().isoformat(timespec='seconds')}", f"- agenticOS commit: {commit}",
                 f"- result: **{'PASS' if ok else 'FAIL'}** ({sum(c for c, _ in self.checks)}/{len(self.checks)} checks)",
                 "", "## Checks", ""]
        lines += [f"- {'✅' if c else '❌'} {w}" for c, w in self.checks]
        lines += ["", "## Timeline (driver)", "", "```", *self.notes, "```"]
        lessons_dump = []
        for p in ("api", "web"):
            d = self.home / "data" / "lessons" / p
            for f in sorted(d.glob("*.md")) if d.is_dir() else []:
                lessons_dump.append(f"--- {p}/{f.name}\n{f.read_text()}")
        self.out["lesson files"] = "\n".join(lessons_dump)
        self.out["costs"] = "\n".join(f"task {r['task']} {r['role']:<9} {r['usd']:.4f} {r['unit']}"
                                      for r in self.board()._rows("SELECT * FROM costs ORDER BY id", ()))
        for title, body in self.out.items():
            lines += ["", f"## {title}", "", "```", (body or "").strip() or "(empty)", "```"]
        path = Path(self.args.report).expanduser().resolve()
        path.write_text("\n".join(lines) + "\n")
        return path

    def cleanup(self) -> None:
        if self.args.keep:
            self.note(f"kept {self.root} (AOS_HOME={self.home})")
            return
        shutil.rmtree(self.root, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", choices=["claude", "kiro"], default="claude")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--timeout", type=float, default=20, help="minutes per feature (default 20)")
    ap.add_argument("--report", default="learning-e2e-report.md")
    run = Run(ap.parse_args())
    try:
        run.setup()
        run.drive()
    finally:
        path = run.report()
        run.cleanup()
    failed = [w for c, w in run.checks if not c]
    print(f"\nreport: {path}")
    print("PASS" if not failed else "FAIL:\n  " + "\n  ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
