"""Claude Code worker: everything passed as flags (spec §6.2)."""

from __future__ import annotations

import os
import uuid

from ..errors import AosError
from ..store import read_text
from .common import Launch, RunSpec, write_mcp_config

EXIT_REASONS: dict[int, str] = {}


def translate(allowed: list[str]) -> list[str]:
    out: list[str] = []
    for a in allowed:
        if a == "read":
            out += ["Read", "Glob", "Grep"]
        elif a == "write":
            out += ["Edit", "Write"]
        elif a.startswith("shell:"):
            out.append(f"Bash({a[6:]}:*)")
        elif a.startswith("mcp:"):
            out.append(f"mcp__{a[4:]}")
        else:
            raise AosError(f"invalid allowed tool {a!r}")
    return list(dict.fromkeys(out))


def prepare(spec: RunSpec, settings: dict) -> Launch:
    mcp = write_mcp_config(spec)
    argv = [settings.get("bin") or "claude", "-p", spec.prompt,
            "--append-system-prompt", read_text(spec.context_file),
            "--mcp-config", str(mcp), "--strict-mcp-config",
            "--permission-mode", "acceptEdits"]
    if settings.get("model"):
        argv += ["--model", str(settings["model"])]
    if spec.resume and spec.session_id:
        session = spec.session_id
        argv += ["--resume", session]
    else:
        session = str(uuid.uuid4())
        argv += ["--session-id", session]
    argv += ["--allowedTools", *translate(spec.allowed)]
    return Launch(argv=argv, cwd=spec.worktree, env=dict(os.environ), session_id=session)
