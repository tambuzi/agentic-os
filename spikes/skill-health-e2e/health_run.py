"""Spike: skill health on real Claude Code sessions, end to end (skill health, plan Task 6).

Throwaway. A throwaway AOS_HOME points at a throwaway copy of this repo with one extra skill,
`demo-naming`, whose step is wrong for the demo project (CamelCase file names). Five real headless
sessions use it (`claude -p`); in three the user corrects the agent ("no, snake_case"). Real hooks:
SessionEnd → `aos lessons capture` → skill uses recorded → background job → skill improver. Checks:
  1. five uses recorded, three corrected
  2. `aos skills health` flags demo-naming
  3. a skill-health proposal for demo-naming lands in the inbox

  ~/.agenticos/venv/bin/python spikes/skill-health-e2e/health_run.py
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
SKILL = """---
name: demo-naming
description: Use when creating a new Python module in the demo project.
---

1. Name the module file in CamelCase, e.g. `DataLoader.py`.
2. Put one function in it, with a docstring.
3. Report the file name.
"""


class Run:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="aos-health-e2e-", dir=Path.home()))
        self.home, self.repo, self.demo = self.root / "home", self.root / "repo", self.root / "demo"
        self.env = {**os.environ, "AOS_HOME": str(self.home)}
        self.env.pop("AOS_REPO", None)
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
        self.repo.mkdir()
        for f in ("SOUL.md", "AGENTS.md", "aos.yaml"):
            if (REPO / f).exists():
                shutil.copy(REPO / f, self.repo / f)
        shutil.copytree(REPO / "skills", self.repo / "skills")
        (self.repo / "skills" / "demo-naming").mkdir()
        (self.repo / "skills" / "demo-naming" / "SKILL.md").write_text(SKILL)
        for a in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True)
        self.sh([*AOS, "init", str(self.repo), "--worker", "claude"])
        (self.home / "acp").symlink_to(Path.home() / ".agenticos" / "acp")
        self.demo.mkdir()
        (self.demo / "README.md").write_text("# demo\n\nModule file names are snake_case.\n")
        for a in (["init", "-q"], ["config", "core.hooksPath", "/dev/null"], ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(["git", *a], cwd=self.demo, check=True, capture_output=True)
        code, out, err = self.sh([*AOS, "link", str(self.demo), "--name", "demo", "--no-graphskill", "--targets", "claude"])
        self.check(code == 0 and (self.demo / ".claude" / "skills" / "demo-naming").exists(),
                   "aos link rendered the demo-naming skill")

    def session(self, module: str, correct: bool) -> str:
        base = ["claude", "-p", "--output-format", "json", "--permission-mode", "acceptEdits", "--model", "haiku"]
        code, out, err = self.sh([*base, f"Use the demo-naming skill to create a module for {module}."], cwd=self.demo)
        sid = json.loads(out)["session_id"]
        if correct:
            self.sh([*base, "--resume", sid, "No, module file names are snake_case in this project, not CamelCase: "
                     "rename the file you created to snake_case."], cwd=self.demo)
        self.note(f"session {sid} ({module}): {'corrected' if correct else 'ok'}")
        return sid

    def wait(self, cond, timeout=300):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(3)
        return False

    def drive(self):
        os.environ["AOS_HOME"] = str(self.home)
        from aos import inbox, skill_health
        data = self.home / "data"
        uses = lambda: [r for r in skill_health.read_uses(data) if r["skill"] == "demo-naming"]
        for i, (module, correct) in enumerate([("loading data", False), ("parsing dates", False),
                                               ("sending emails", True), ("caching results", True),
                                               ("reading config", True)]):
            self.session(module, correct)
            self.wait(lambda: len(uses()) >= i + 1, timeout=60)
        self.note("uses: " + json.dumps([(r["outcome"], r["session"][:8]) for r in uses()]))
        self.check(len(uses()) == 5 and sum(r["outcome"] == "corrected" for r in uses()) == 3,
                   "five uses recorded, three corrected")
        out = self.sh([*AOS, "skills", "health"])[1]
        self.note("aos skills health:\n" + out)
        self.check(any("demo-naming" in l and "flagged" in l for l in out.splitlines()), "demo-naming is flagged")
        props = lambda: [p for p in inbox.list_proposals(data) if p.get("source") == "skill-health"]
        if not self.wait(lambda: bool(props()), timeout=300):
            self.note("no proposal from the background job; running `aos lessons learn-sessions` by hand")
            self.note(self.sh([*AOS, "lessons", "learn-sessions"])[1])
        ps = props()
        for p in ps:
            self.note("proposal: " + json.dumps({k: p.get(k) for k in ("kind", "op", "reason", "args")})[:1500])
        self.check(len(ps) == 1 and ps[0]["args"]["name"] == "demo-naming", "a skill-health proposal in the inbox")

    def report(self):
        ok = all(c for c, _ in self.checks)
        lines = ["# Skill health end-to-end (claude)", "", f"- date: {datetime.datetime.now():%Y-%m-%dT%H:%M}",
                 f"- result: **{'PASS' if ok else 'FAIL'}** ({sum(c for c, _ in self.checks)}/{len(self.checks)})", "",
                 "## Checks", ""] + [f"- {'✅' if c else '❌'} {w}" for c, w in self.checks] + \
                ["", "## Driver", "", "```", *self.notes, "```"]
        for name in ("sessions.log", "improve-demo-naming.log"):
            log = self.home / "data" / "logs" / name
            if log.exists():
                lines += ["", f"## {name} (tail)", "", "```", *log.read_text().splitlines()[-30:], "```"]
        out = Path.cwd() / "health-e2e-report.md"
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
        from aos.sessions import claude_project_folder
        shutil.rmtree(Path.home() / ".claude" / "projects" / claude_project_folder(r.demo), ignore_errors=True)
        shutil.rmtree(r.root, ignore_errors=True)
    sys.exit(0 if ok else 1)
