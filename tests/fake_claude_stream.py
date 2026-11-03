"""A fake `claude -p --output-format stream-json`: replays a recorded stream (skill test runner tests).

FAKE_CLAUDE_STREAM: the recorded stream to print on the first turn (later turns print only a result).
FAKE_CLAUDE_LOG: one JSON line per call with argv, cwd and the aos env, for the tests to inspect.
A prompt containing SLEEP hangs, to exercise the scenario timeout.
"""

import json
import os
import sys
import time
from pathlib import Path

from aos.sessions import claude_project_folder

argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "AOS_HOME": os.environ.get("AOS_HOME"),
                         "AOS_DRY_DISPATCH": os.environ.get("AOS_DRY_DISPATCH"),
                         "AOS_SKILL_TEST": os.environ.get("AOS_SKILL_TEST")}) + "\n")
# Claude Code keeps a transcript per project folder; the runner must clean it up
t = Path.home() / ".claude" / "projects" / claude_project_folder(os.getcwd())
t.mkdir(parents=True, exist_ok=True)
(t / "sess-1.jsonl").write_text("{}\n")
if "SLEEP" in prompt:
    time.sleep(60)
if "--resume" in argv:
    print(json.dumps({"type": "result", "subtype": "success", "session_id": "sess-1", "total_cost_usd": 0.03}))
else:
    sys.stdout.write(Path(os.environ["FAKE_CLAUDE_STREAM"]).read_text())
