"""Spike: worker-to-worker messages end to end with a real agent tool (Claude or Kiro).

Throwaway verification, not product code. In a throwaway AOS_HOME with two demo projects that
share an orders payload, two tasks of one feature run at the same time:

  #1 api: rename the order field `total` to `total_cents` in api.py and broadcast it
  #2 web: show the order total in client.py; told that the api task may change the payload,
          and to ask the api task whether amounts are in cents

Checks: #1 broadcast; the broadcast reached #2 (live or at board_read); client.py ends up using
total_cents; #2 asked #1 a question and it was answered (by #1 or by the automatic reply).

  ~/.agenticos/venv/bin/python spikes/messages-e2e/run.py --tool claude   # or --tool kiro
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

API_SPEC = ("In api.py, rename the `total` field of the order dict to `total_cents` (an int, the amount in "
            "cents). Update test_api.py and run `python3 test_api.py`. As soon as the rename is done, call "
            "task_broadcast to tell the other workers the field is now total_cents. Answer any question "
            "other workers ask you with task_reply. Then commit and finish.")
WEB_SPEC = ("client.py has summary(order) that formats an order from the api. Make it return "
            "'Total: <amount> EUR' using the order's amount field, and add test_client.py that checks it. "
            "The api task is changing the order payload at the same time: before you write the code, ask "
            "the api task with task_message(question=true) which field holds the amount and whether it is "
            "in cents, and follow any message from other workers. Call board_read before finishing. "
            "Run `python3 test_client.py`, commit and finish.")


class Run:
    def __init__(self, args):
        self.args = args
        self.root = Path(tempfile.mkdtemp(prefix="aos-messages-e2e-", dir=Path.home()))
        self.home = self.root / "home"
        self.env = {**os.environ, "AOS_HOME": str(self.home), "PYTHONPATH": str(REPO)}
        self.notes: list[str] = []
        self.checks: list[tuple[bool, str]] = []
        self.out: dict[str, str] = {}

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

    def project(self, name: str, files: dict[str, str]) -> None:
        d = self.root / name
        d.mkdir()
        for f, body in files.items():
            (d / f).write_text(body)
        git = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
        git("init", "-q")
        git("config", "core.hooksPath", "/dev/null")
        git("add", ".")
        git("commit", "-qm", "init")
        self.aos("link", str(d), "--name", name, "--no-graphskill")
        git("add", "-A")
        git("commit", "-qm", "aos link")

    def setup(self) -> None:
        self.aos("init", str(REPO))
        self.aos("worker", self.args.tool)
        adapter = Path.home() / ".agenticos" / "acp"
        if self.args.tool == "claude" and adapter.is_dir():
            (self.home / "acp").symlink_to(adapter)
        self.project("api", {"api.py": 'def order():\n    return {"id": 1, "total": 1999}\n',
                             "test_api.py": 'from api import order\nassert order()["total"] == 1999\nprint("ok")\n'})
        self.project("web", {"client.py": 'def summary(order):\n    return f"Order {order[\'id\']}"\n'})
        self.out["doctor"] = self.aos("doctor", check=False)
        self.check(f"{self.args.tool}: acp" in self.out["doctor"], f"doctor: {self.args.tool} runs over ACP")

    def drive(self) -> None:
        (self.root / "api.md").write_text(API_SPEC)
        (self.root / "web.md").write_text(WEB_SPEC)
        self.aos("feature", "new", "orders", "--title", "Orders in cents", "--projects", "api,web")
        self.aos("task", "add", "orders", "api", "Rename total to total_cents", "--spec-file", str(self.root / "api.md"))
        self.aos("task", "add", "orders", "web", "Show the order total", "--spec-file", str(self.root / "web.md"))
        log = open(self.root / "dispatcher.log", "w")
        disp = subprocess.Popen([*AOS, "board", "run", "--feature", "orders", "--until-done", "--parallel", "2"],
                                env=self.env, cwd=self.root, stdout=log, stderr=subprocess.STDOUT)
        b, deadline = self.board(), time.monotonic() + 60 * self.args.timeout
        try:
            while disp.poll() is None and time.monotonic() < deadline:
                for p in b.permissions(status="pending"):
                    self.note(f"approve permission #{p['id']} ({p['role']}): {p['summary'][:90]}")
                    self.aos("task", "approve", str(p["id"]), check=False)
                for q in b.open_questions("orders"):
                    if q["escalated"]:
                        self.note(f"planner answers escalated question #{q['id']}")
                        b.answer(q["id"], "Amounts are integer cents in total_cents; show them as euros with two decimals.")
                time.sleep(2)
        finally:
            if disp.poll() is None:
                disp.send_signal(2); time.sleep(3); disp.send_signal(2); disp.wait(30)
            log.close()

        b = self.board()
        events = b.events("orders")
        self.out["timeline"] = "\n".join(f"{e['id']:>3} #{e['task'] or '-'} {e['kind']:<17} {(e['body'] or '')[:160]}"
                                         for e in events)
        self.out["messages"] = self.aos("task", "show", "2", "--messages", check=False)
        broadcasts = [m for m in b.thread(1) if m["kind"] == "message" and m["from"] == 1 and m["to"] == "all"]
        self.check(bool(broadcasts), "api broadcast the rename")
        bid = broadcasts[0]["id"] if broadcasts else None
        live = any(e["kind"] in ("message_delivered", "message_queued") and e["task"] == 2
                   and (e["body"] or "").startswith(f"#{bid} ") for e in events)
        started = [e["id"] for e in events if e["task"] == 2 and e["kind"] == "status"
                   and (e["body"] or "").startswith("attempt") and "started" in (e["body"] or "")]
        via_context = bool(bid and started and started[0] > bid)
        self.check(live or via_context, "the broadcast reached web (" + ("live" if live else "in its starting context"
                                         if via_context else "not at all") + ")")
        self.note("the two tasks ran " + ("at the same time" if started and bid and started[0] < bid else
                  "one after the other (the memory gate allows one worker at a time when free memory is low)"))
        questions = [m for m in b.thread(2) if m["kind"] == "message" and m["from"] == 2 and m["question"]]
        self.check(bool(questions), "web asked the api task a question")
        answered = [q for q in questions if any(r["kind"] == "reply" and r["re"] == q["id"] for r in b.thread(2))]
        self.check(bool(answered), "web's question was answered")
        for tid in (1, 2):
            self.check(b.task(tid)["status"] == "done", f"task #{tid} done (is {b.task(tid)['status']})")
        client = self.home / "worktrees" / "orders" / "web" / "client.py"
        text = client.read_text() if client.exists() else ""
        self.out["client.py"] = text
        self.check("total_cents" in text, "client.py uses total_cents")

    def report(self) -> Path:
        ok = all(c for c, _ in self.checks)
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        lines = [f"# Worker messages end-to-end ({self.args.tool})", "",
                 f"- date: {datetime.datetime.now().isoformat(timespec='seconds')}", f"- agenticOS commit: {commit}",
                 f"- result: **{'PASS' if ok else 'FAIL'}** ({sum(c for c, _ in self.checks)}/{len(self.checks)} checks)",
                 "", "## Checks", ""] + [f"- {'✅' if c else '❌'} {w}" for c, w in self.checks]
        lines += ["", "## Driver", "", "```", *self.notes, "```"]
        for title, body in self.out.items():
            lines += ["", f"## {title}", "", "```", (body or "").strip() or "(empty)", "```"]
        path = Path(self.args.report).expanduser().resolve()
        path.write_text("\n".join(lines) + "\n")
        return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", choices=["claude", "kiro"], default="claude")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--report", default="messages-e2e-report.md")
    run = Run(ap.parse_args())
    try:
        run.setup()
        run.drive()
    finally:
        path = run.report()
        if not run.args.keep:
            shutil.rmtree(run.root, ignore_errors=True)
    failed = [w for c, w in run.checks if not c]
    print(f"\nreport: {path}")
    print("PASS" if not failed else "FAIL:\n  " + "\n  ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
