#!/usr/bin/env python3
"""Fake kiro-cli for dispatcher tests: records whether its agent file exists, then exits FAKE_KIRO_EXIT."""
import os
import sys
from pathlib import Path

name = sys.argv[sys.argv.index("--agent") + 1]
agent = Path(os.environ["FAKE_KIRO_AGENTS"]) / f"{name}.json"
Path(os.environ["FAKE_KIRO_MARKER"]).write_text("agent file present" if agent.exists() else "agent file MISSING")
sys.exit(int(os.environ.get("FAKE_KIRO_EXIT", "3")))
