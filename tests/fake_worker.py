"""Fake board worker for dispatcher tests. Mode from FAKE_MODE_<task id> or FAKE_MODE."""

import os
import sys
import time
from pathlib import Path

from aos.board import Board
from aos.config import data_root

tid = int(sys.argv[1])
role = sys.argv[2] if len(sys.argv) > 2 else "worker"
if os.environ.get("FAKE_COST"):  # what `claude -p --output-format json` prints at the end
    import atexit
    import json as _json
    atexit.register(lambda: print(_json.dumps({"type": "result", "total_cost_usd": float(
        os.environ.get("FAKE_REVIEW_COST" if role == "reviewer" else "FAKE_COST", os.environ["FAKE_COST"]))}),
        flush=True))
if role == "reviewer":
    from aos.board import Board as _B
    from aos.config import data_root as _dr
    _b = _B(_dr())
    _gen = _b.task(tid)["generation"]
    verdict = os.environ.get("FAKE_REVIEW", "pass")
    if verdict == "fail_once":  # fail the first review of this task, pass the next
        marker = Path(os.environ["FAKE_MARKER_DIR"]) / f"reviewed-{tid}"
        verdict = "pass" if marker.exists() else "fail"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("x")
    if verdict == "pass":
        _b.review_verdict(tid, True, "looks right: spec and contract met", generation=_gen)
    elif verdict == "fail":
        _b.review_verdict(tid, False, "orders.py:3 total ignores the discount; expected 90.0 for SAVE10 on 100",
                          generation=_gen)
    sys.exit(0)  # verdict "none": exit without a verdict
mode = os.environ.get(f"FAKE_MODE_{tid}", os.environ.get("FAKE_MODE", "complete"))
board = Board(data_root())
if mode == "crash":
    sys.exit(2)
if mode == "noop":
    sys.exit(0)
if mode == "sleep":
    time.sleep(float(os.environ.get("FAKE_SLEEP", "30")))
    sys.exit(0)
if mode == "block":
    board.block(tid, "fake blocker", author=f"task:{tid}")
    sys.exit(0)
if mode == "complete_then_sleep":
    board.mark_seen(tid)
    board.complete(tid, "done, still wrapping up", author=f"task:{tid}")
    time.sleep(float(os.environ.get("FAKE_SLEEP", "30")))
    sys.exit(0)
if mode == "block_then_sleep":
    board.block(tid, "fake blocker", author=f"task:{tid}")
    time.sleep(float(os.environ.get("FAKE_SLEEP", "30")))
    sys.exit(0)
if mode in ("commit_complete", "nocommit_complete"):
    import subprocess
    if mode == "commit_complete":
        (Path.cwd() / f"t{tid}-{os.getpid()}.txt").write_text("work\n")
        (Path.cwd() / f"scratch-{tid}-{os.getpid()}.tmp").write_text("untracked leftover\n")
        subprocess.run(["git", "add", f"t{tid}-{os.getpid()}.txt"], check=True)
        subprocess.run(["git", "commit", "-qm", f"task {tid}"], check=True)
    board.mark_seen(tid)
    board.complete(tid, f"claimed done by fake worker ({mode})", author=f"task:{tid}", to="review")
    sys.exit(0)
if mode == "write":
    (Path.cwd() / f"t{tid}.txt").write_text("work\n")
board.mark_seen(tid)
board.complete(tid, f"done by fake worker in {Path.cwd()}", author=f"task:{tid}")
