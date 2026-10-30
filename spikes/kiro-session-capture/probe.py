"""Spike: how does Kiro expose a finished conversation? (session learning, plan Task 0)

Throwaway. Run it in a Kiro project on the Kiro machine:

  python3 spikes/kiro-session-capture/probe.py install <project-dir>   # adds temporary hooks
  # open Kiro in <project-dir>, chat a little and type the phrase it prints, end the turn/session
  python3 spikes/kiro-session-capture/probe.py report <project-dir>    # writes the findings
  python3 spikes/kiro-session-capture/probe.py remove <project-dir>    # removes the temporary hooks

It installs, for both Kiro IDE and kiro-cli:
  - an IDE hook  .kiro/hooks/aos-probe.kiro.hook  (when agentStop → runCommand)
  - a workspace hook entry in .kiro/hooks/aos-probe.json (trigger "agentStop" / "Stop")
each running this script with `dump`, which saves stdin and the environment. `report` then
also searches ~/.kiro and the Kiro app-support folders for the phrase, to find where
conversations are stored.
"""

import json
import os
import sys
import time
from pathlib import Path

PHRASE = "aos-probe-banana-42"
OUT = Path.home() / ".aos-kiro-probe"


def install(project: Path) -> None:
    hooks = project / ".kiro" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    cmd = f"{sys.executable} {Path(__file__).resolve()} dump"
    (hooks / "aos-probe.kiro.hook").write_text(json.dumps({
        "name": "aos probe", "version": "1.0.0", "enabled": True,
        "description": "temporary: records what Kiro passes at agentStop",
        "when": {"type": "agentStop"}, "then": {"type": "runCommand", "command": cmd}}, indent=2))
    (hooks / "aos-probe.json").write_text(json.dumps({"version": "v1", "hooks": [
        {"name": "aos probe stop", "trigger": t, "action": {"type": "command", "command": cmd}, "timeout": 30}
        for t in ("agentStop", "Stop", "SessionEnd")]}, indent=2))
    OUT.mkdir(exist_ok=True)
    print(f"installed. In Kiro, chat a little and include this phrase in a message: {PHRASE}")


def dump() -> None:
    OUT.mkdir(exist_ok=True)
    data = sys.stdin.read() if not sys.stdin.isatty() else ""
    (OUT / f"dump-{time.time_ns()}.json").write_text(json.dumps(
        {"argv": sys.argv, "cwd": os.getcwd(), "stdin": data[:20000],
         "env": {k: v for k, v in os.environ.items() if "KIRO" in k.upper() or "HOOK" in k.upper()
                 or k in ("PWD", "SHELL")}}, indent=2))


def report(project: Path) -> None:
    dumps = sorted(OUT.glob("dump-*.json"))
    lines = ["# Kiro session capture probe", "", f"- dumps from hooks: {len(dumps)}"]
    for d in dumps[-5:]:
        lines += ["", f"## {d.name}", "", "```json", d.read_text()[:4000], "```"]
    roots = [Path.home() / ".kiro", Path.home() / "Library" / "Application Support" / "Kiro",
             Path.home() / "Library" / "Application Support" / "kiro-cli", Path.home() / ".local" / "share" / "kiro-cli"]
    found = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            try:
                if p.is_file() and p.stat().st_size < 50_000_000 and PHRASE.encode() in p.read_bytes():
                    found.append(f"{p}  ({p.stat().st_size} bytes)")
            except OSError:
                continue
    lines += ["", "## Files containing the phrase", ""] + ([f"- {f}" for f in found] or ["- none found"])
    out = Path(__file__).resolve().parent / f"FINDINGS-{time.strftime('%Y-%m-%d')}.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


def remove(project: Path) -> None:
    for name in ("aos-probe.kiro.hook", "aos-probe.json"):
        (project / ".kiro" / "hooks" / name).unlink(missing_ok=True)
    print("removed the temporary hooks")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "dump":
        dump()
    elif cmd in ("install", "report", "remove") and len(sys.argv) > 2:
        {"install": install, "report": report, "remove": remove}[cmd](Path(sys.argv[2]).expanduser().resolve())
    else:
        print(__doc__)
