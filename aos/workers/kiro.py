"""Kiro worker: a per-run agent file plus `kiro-cli chat --no-interactive` (spec §6.3).

Not yet verified against a live kiro-cli: the shell allow-list uses the legacy
toolsSettings.shell.allowedCommands regex form, and no session id is recorded
(resume continues the worktree's last session).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..errors import AosError
from ..store import write_atomic
from .common import Launch, RunSpec

EXIT_REASONS = {3: "MCP startup failed"}


def agents_dir(settings: dict) -> Path:
    return Path(settings.get("agents_dir") or Path.home() / ".kiro" / "agents").expanduser()


def agent_name(task: dict) -> str:
    return f"aos-{task['feature']}-t{task['id']}"


def agent_config(spec: RunSpec, settings: dict) -> dict:
    tools, allowed, shell = ["read", "write"], [], []
    for a in spec.allowed:
        if a in ("read", "write"):
            allowed.append(a)
        elif a.startswith("shell:"):
            # the prefix plus plain arguments only: no chaining, pipes, substitution,
            # redirection or newlines, so `git status && curl …` is not allowed
            shell.append(rf"^{re.escape(a[6:])}(?:[ \t]+[^;&|`$()<>\\\r\n]*)?$")
        elif a.startswith("mcp:"):
            if a[4:] in spec.mcp_servers:
                tools.append(f"@{a[4:]}")
                allowed.append(f"@{a[4:]}")
        else:
            raise AosError(f"invalid allowed tool {a!r}")
    if shell:
        tools.append("shell")
    cfg = {
        "name": agent_name(spec.task),
        "description": f"agenticOS worker for task #{spec.task['id']}",
        "prompt": f"file://{spec.context_file}",
        "mcpServers": spec.mcp_servers,
        "includeMcpJson": False,
        "tools": list(dict.fromkeys(tools)),
        "allowedTools": list(dict.fromkeys(allowed)),
    }
    if shell:
        from ..acp.permissions import HOOK_BYPASS_PATTERNS
        cfg["toolsSettings"] = {"shell": {"allowedCommands": shell, "deniedCommands": list(HOOK_BYPASS_PATTERNS)}}
    if settings.get("model"):
        cfg["model"] = settings["model"]
    return cfg


def prepare(spec: RunSpec, settings: dict) -> Launch:
    name = agent_name(spec.task)
    path = agents_dir(settings) / f"{name}.json"
    write_atomic(path, json.dumps(agent_config(spec, settings), indent=2) + "\n")
    argv = [settings.get("bin") or "kiro-cli", "chat", "--no-interactive", "--agent", name,
            "--require-mcp-startup"]
    if spec.resume:
        argv.append("--resume")
    argv += ["--", spec.prompt]  # "--": a prompt starting with "-" is never read as a flag
    return Launch(argv=argv, cwd=spec.worktree, env=dict(os.environ), session_id=None,
                  cleanup=lambda: path.unlink(missing_ok=True))
