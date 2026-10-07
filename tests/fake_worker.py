"""Fake board worker for dispatcher tests. Mode from FAKE_MODE_<task id> or FAKE_MODE."""

import os
import sys
import time
from pathlib import Path

from aos.board import Board
from aos.config import data_root

tid = int(sys.argv[1])
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
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-qm", f"task {tid}"], check=True)
    board.mark_seen(tid)
    board.complete(tid, f"claimed done by fake worker ({mode})", author=f"task:{tid}", to="review")
    sys.exit(0)
if mode == "write":
    (Path.cwd() / f"t{tid}.txt").write_text("work\n")
board.mark_seen(tid)
board.complete(tid, f"done by fake worker in {Path.cwd()}", author=f"task:{tid}")
