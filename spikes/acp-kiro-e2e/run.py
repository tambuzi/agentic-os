"""Spike: the ACP board workers end to end with real Kiro (`kiro-cli acp`).

Throwaway verification, not product code. It runs the scenario from the ACP plan's Task 9
on a machine with kiro-cli, in a throwaway AOS_HOME so real business data is untouched:

  task 1 "Add mul": a queued steer is sent while Kiro waits on a permission, then every
                    permission is approved (worker and reviewer)  -> steer_queued, done
  task 2 "Add div": once Kiro is working, `aos task steer --now` interrupts the turn
                    -> open permission cancelled, re-prompt with the new instruction, done

It drives the real CLI (`aos task steer`, `aos task approve`), checks the outcome and writes a
markdown report (default ./kiro-e2e-report.md) to send back.

  ~/.agenticos/venv/bin/python spikes/acp-kiro-e2e/run.py            # real Kiro
  ~/.agenticos/venv/bin/python spikes/acp-kiro-e2e/run.py --fake     # rehearsal with the test agent
Options: --keep (leave the test home), --timeout MIN (default 30), --report PATH.
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

SPEC1 = """Add a function `mul(a, b)` to calc.py that returns a * b, and add an assertion for it to test_calc.py.
Then run `python3 test_calc.py` in the shell to check it passes, and commit your change with git.
"""
SPEC2 = """Add a function `div(a, b)` to calc.py (raise ValueError on b == 0) with assertions in test_calc.py.
Run `python3 test_calc.py` in the shell, then commit with git.
"""
STEER1 = "Also give mul a one-line docstring: Multiply two numbers."
STEER2 = "Stop. Use ZeroDivisionError instead of ValueError."
AOS = [sys.executable, "-c", "import sys; from aos.cli import main; sys.exit(main(sys.argv[1:]))"]


class Run:
    def __init__(self, args):
        self.args = args
        self.root = Path(tempfile.mkdtemp(prefix="aos-kiro-e2e-", dir=Path.home()))
        self.home = self.root / "home"
        self.demo = self.root / "demo"
        self.repo = REPO
        self.env = {**os.environ, "AOS_HOME": str(self.home), "PYTHONPATH": str(REPO)}
        self.notes: list[str] = []
        self.checks: list[tuple[bool, str]] = []
        self.out: dict[str, str] = {}

    # -- helpers ---------------------------------------------------------------
    def aos(self, *argv, check=True) -> str:
        r = subprocess.run([*AOS, *argv], env=self.env, capture_output=True,
                           text=True, cwd=self.root)
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

    def board(self):
        from aos.board import Board
        return Board(self.home / "data")

    # -- setup -----------------------------------------------------------------
    def setup(self) -> None:
        if self.args.fake:  # a copy of the repo whose kiro profile runs the test agent
            self.repo = self.root / "repo"
            shutil.copytree(REPO, self.repo, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".superpowers"))
            import yaml
            cfg = yaml.safe_load((self.repo / "aos.yaml").read_text())
            cfg["workers"]["kiro"]["acp_command"] = [sys.executable, str(REPO / "tests" / "fake_acp_agent.py")]
            (self.repo / "aos.yaml").write_text(yaml.safe_dump(cfg))
            self.env.update({"FAKE_ACP_TOOL": "kiro", "FAKE_ACP_MODE_1": "permission", "FAKE_ACP_MODE_2": "slow",
                             "FAKE_ACP_SLOW": "25", "FAKE_ACP_LOG": str(self.root / "fake-agent.events")})
        elif not shutil.which("kiro-cli"):
            raise SystemExit("kiro-cli not on PATH (or use --fake for a rehearsal)")
        self.aos("init", str(self.repo))
        self.aos("worker", "kiro")
        self.out["doctor"] = self.aos("doctor", check=False)
        self.check("kiro: acp" in self.out["doctor"], "doctor: kiro runs over ACP")
        self.check("board work uses kiro (global (aos worker))" in self.out["doctor"], "doctor: global setting is kiro")

        self.demo.mkdir()
        (self.demo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (self.demo / "test_calc.py").write_text('from calc import add\nassert add(2, 3) == 5\nprint("ok")\n')
        git = lambda *a: subprocess.run(["git", *a], cwd=self.demo, check=True, capture_output=True)
        git("init", "-q"); git("add", "."); git("commit", "-qm", "init")
        self.aos("link", str(self.demo), "--name", "demo", "--no-graphskill")
        git("add", "-A"); git("commit", "-qm", "aos link")
        import yaml
        p = self.home / "data" / "projects.yaml"
        d = yaml.safe_load(p.read_text())
        d["projects"]["demo"]["worker"] = {"allowed_tools": ["read", "write", "mcp:aos"],
                                           "verify_command": "python3 test_calc.py"}
        p.write_text(yaml.safe_dump(d))
        (self.root / "spec1.md").write_text(SPEC1)
        (self.root / "spec2.md").write_text(SPEC2)
        self.aos("feature", "new", "acpdemo", "--title", "ACP demo", "--projects", "demo")
        self.aos("task", "add", "acpdemo", "demo", "Add mul", "--spec-file", str(self.root / "spec1.md"))
        self.aos("task", "add", "acpdemo", "demo", "Add div", "--spec-file", str(self.root / "spec2.md"), "--after", "1")
        self.note(f"test home {self.home}")

    # -- the scenario ----------------------------------------------------------
    def drive(self) -> None:
        log = open(self.root / "dispatcher.log", "w")
        disp = subprocess.Popen([*AOS, "board", "run", "--feature", "acpdemo", "--until-done"],
                                env=self.env, cwd=self.root, stdout=log, stderr=subprocess.STDOUT)
        self.note(f"dispatcher started (pid {disp.pid})")
        b = self.board()
        steered1 = steered2 = False
        running2_since = None
        deadline = time.monotonic() + 60 * self.args.timeout
        try:
            while disp.poll() is None and time.monotonic() < deadline:
                tasks = {t["id"]: t for t in b.tasks(feature="acpdemo")}
                for p in b.permissions(status="pending"):
                    if p["task"] == 1 and not steered1:
                        self.note(f"#1 waits on permission #{p['id']} ({p['summary'][:60]}); steering before approving")
                        self.aos("task", "steer", "1", STEER1)
                        steered1 = True
                        time.sleep(2)
                    if p["task"] == 2 and not steered2:
                        continue  # let --now meet an open permission if there is one
                    self.note(f"approve permission #{p['id']} for #{p['task']} ({p['role']}): {p['summary'][:80]}")
                    self.aos("task", "approve", str(p["id"]), check=False)
                t2 = tasks.get(2)
                if t2 and t2["status"] == "running" and not steered2:
                    running2_since = running2_since or time.monotonic()
                    if time.monotonic() - running2_since > 8:
                        self.note("#2 is working; steer --now")
                        self.aos("task", "steer", "2", STEER2, "--now")
                        steered2 = True
                if all(t["status"] in ("done", "failed", "blocked", "cancelled") for t in tasks.values()) and tasks:
                    time.sleep(3)
                    break
                time.sleep(2)
        finally:
            if disp.poll() is None:
                self.note("stopping the dispatcher")
                disp.send_signal(2)
                try:
                    disp.wait(20)
                except subprocess.TimeoutExpired:
                    disp.send_signal(2)
                    disp.wait(20)
            log.close()
        self.note(f"dispatcher exited ({disp.returncode})")
        self.check(steered1, "task 1 hit a permission request (so the steer went in mid-turn)")
        self.check(steered2, "task 2 ran long enough to steer --now")

    # -- checks and report -----------------------------------------------------
    def verify(self) -> None:
        b = self.board()
        tasks = {t["id"]: t for t in b.tasks(feature="acpdemo")}
        events = b.events("acpdemo", limit=1000)
        ev = lambda tid, kind: [e for e in events if e["task"] == tid and e["kind"] == kind]
        for tid in (1, 2):
            t = tasks.get(tid) or {}
            self.check(t.get("status") == "done", f"task {tid} done (is {t.get('status')})")
            self.check(t.get("transport") == "acp", f"task {tid} ran over ACP (transport {t.get('transport')})")
        self.check(ev(1, "steer_queued"), "task 1: steer recorded as steer_queued")
        self.check(any("turn cancelled" in e["body"] for e in ev(2, "steer_delivered")), "task 2: --now cancelled the turn")
        self.check(not any("ACP unavailable" in e["body"] for e in events), "no fallback to the CLI")
        self.check(not b.permissions(status="pending"), "no permission left pending")
        costs = b._rows("SELECT task, role, usd, unit FROM costs ORDER BY id", ())
        self.check(costs and all(c["unit"] == "credit" for c in costs) and sum(c["usd"] for c in costs) > 0,
                   "costs recorded in Kiro credits")
        leftovers = sorted(p.name for p in (Path.home() / ".kiro" / "agents").glob("aos-acpdemo-*.json"))
        self.check(not leftovers, f"per-run Kiro agent files removed {leftovers or ''}")
        wt = self.home / "worktrees" / "acpdemo" / "demo"
        calc = (wt / "calc.py").read_text() if (wt / "calc.py").exists() else ""
        if not self.args.fake:  # the test agent doesn't write code
            self.check("Multiply two numbers" in calc, "steer 1 acted on: mul has the docstring")
            self.check("ZeroDivisionError" in calc, "steer --now acted on: div raises ZeroDivisionError")

        self.out["board"] = self.aos("board", check=False)
        self.out["events"] = "\n".join(f"{e['id']:>3} #{e['task'] or '-'} {e['kind']:<16} {e['body'][:140]}"
                                       for e in events)
        self.out["permissions"] = "\n".join(f"#{p['id']} task {p['task']} {p['role']:<8} {p['status']:<8} {p['summary'][:100]}"
                                            for p in b.permissions())
        self.out["costs"] = "\n".join(f"task {c['task']} {c['role']:<8} {c['usd']:.4f} {c['unit']}" for c in costs)
        self.out["git log"] = subprocess.run(["git", "log", "--oneline"], cwd=wt, capture_output=True, text=True).stdout \
            if wt.exists() else "(no worktree)"
        self.out["calc.py"] = calc
        logs = sorted((self.home / "data" / "logs").glob("*.log"))
        self.out["worker logs (tails)"] = "\n\n".join(
            f"--- {p.name}\n" + "\n".join(p.read_text(errors="replace").splitlines()[-25:]) for p in logs)
        self.out["dispatcher log (tail)"] = "\n".join(
            (self.root / "dispatcher.log").read_text(errors="replace").splitlines()[-40:])
        kiro_mcp = Path.home() / ".kiro" / "settings" / "mcp.json"
        self.out["global Kiro MCP config (server names only)"] = self._mcp_names(kiro_mcp)

    @staticmethod
    def _mcp_names(path: Path) -> str:
        import json
        try:
            return ", ".join(sorted((json.loads(path.read_text()).get("mcpServers") or {}).keys())) or "(none)"
        except (OSError, ValueError):
            return "(no ~/.kiro/settings/mcp.json)"

    def report(self) -> Path:
        ok = all(c for c, _ in self.checks)
        kiro_v = subprocess.run(["kiro-cli", "--version"], capture_output=True, text=True).stdout.strip() \
            if shutil.which("kiro-cli") else "n/a"
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        lines = [f"# Kiro ACP end-to-end {'(FAKE rehearsal)' if self.args.fake else ''}",
                 "", f"- date: {datetime.datetime.now().isoformat(timespec='seconds')}",
                 f"- agenticOS commit: {commit}", f"- kiro-cli: {kiro_v}",
                 f"- result: **{'PASS' if ok else 'FAIL'}** ({sum(c for c, _ in self.checks)}/{len(self.checks)} checks)",
                 "", "## Checks", ""]
        lines += [f"- {'✅' if c else '❌'} {what}" for c, what in self.checks]
        lines += ["", "## Timeline (driver)", "", "```", *self.notes, "```"]
        for title, body in self.out.items():
            lines += ["", f"## {title}", "", "```", body.strip() or "(empty)", "```"]
        path = Path(self.args.report).expanduser().resolve()
        path.write_text("\n".join(lines) + "\n")
        return path

    def cleanup(self) -> None:
        if self.args.keep:
            self.note(f"kept {self.root} (AOS_HOME={self.home})")
            return
        for wt in (self.home / "worktrees").glob("*/*"):
            subprocess.run(["git", "-C", str(self.demo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        shutil.rmtree(self.root, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="rehearse with the test agent instead of kiro-cli")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway AOS_HOME and demo project")
    ap.add_argument("--timeout", type=float, default=30, help="minutes before giving up (default 30)")
    ap.add_argument("--report", default="kiro-e2e-report.md")
    run = Run(ap.parse_args())
    try:
        run.setup()
        run.drive()
        run.verify()
    finally:
        path = run.report()
        run.cleanup()
    failed = [w for c, w in run.checks if not c]
    print(f"\nreport: {path}")
    print("PASS" if not failed else "FAIL:\n  " + "\n  ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
