"""What differs per agent tool over ACP (verified in spikes/acp/FINDINGS.md).

Claude: the claude-agent-acp adapter under Node, with CLAUDE_CODE_EXECUTABLE set; context
appended to Claude Code's system prompt; steering injected mid-turn; cost in USD.
Kiro: `kiro-cli acp` itself; context through a per-run Kiro agent selected as the session
mode; steering queued until the running tool finishes; cost in credits, reported in a
trailing `_kiro.dev/metadata` notification that also marks the real end of a turn.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..errors import AosError

CLAUDE_ADAPTER_PACKAGE = "@agentclientprotocol/claude-agent-acp"
CLAUDE_ADAPTER_VERSION = "0.87.0"

run_command = subprocess.run  # replaced in tests

FACTS = {
    "claude": {"context": "system_prompt", "steer": "injected", "cost_unit": "USD", "turn_end": None},
    "kiro": {"context": "agent_mode", "steer": "queued", "cost_unit": "credit", "turn_end": "_kiro.dev/metadata"},
}


class AdapterMissing(AosError):
    """ACP can't be used for this tool on this machine (callers fall back to the CLI)."""


def acp_home(home: str | Path) -> Path:
    return Path(home) / "acp"


def default_claude_adapter(home: str | Path) -> Path:
    return acp_home(home) / "node_modules" / CLAUDE_ADAPTER_PACKAGE / "dist" / "index.js"


def launch(tool: str, settings: dict, home: str | Path) -> tuple[list[str], dict]:
    """argv and env that start an ACP agent for `tool`. Raises AdapterMissing."""
    env = dict(os.environ)
    if settings.get("acp_command"):  # explicit command (tests, other agents)
        return [str(a) for a in settings["acp_command"]], env
    if tool == "claude":
        entry = Path(settings.get("acp_adapter") or default_claude_adapter(home)).expanduser()
        node, claude = shutil.which("node"), settings.get("bin") or shutil.which("claude")
        missing = [name for name, ok in (("the claude-agent-acp adapter", entry.is_file()), ("node", node),
                                          ("the claude CLI", claude)) if not ok]
        if missing:
            raise AdapterMissing(f"Claude over ACP needs {', '.join(missing)} (run `aos acp install claude`)",
                                 "or set workers.claude.transport: cli")
        env["CLAUDE_CODE_EXECUTABLE"] = claude
        return [node, str(entry)], env
    if tool == "kiro":
        kiro = settings.get("bin") or shutil.which("kiro-cli")
        if not kiro:
            raise AdapterMissing("Kiro over ACP needs kiro-cli on PATH", "install kiro-cli and log in")
        return [kiro, "acp"], env
    raise AdapterMissing(f"no ACP support for worker profile {tool!r}")


def install(tool: str, home: str | Path) -> str:
    """Install what `tool` needs for ACP; returns what was done. Raises AosError."""
    if tool == "kiro":
        return "nothing to install: Kiro speaks ACP itself (`kiro-cli acp`)"
    if tool != "claude":
        raise AosError(f"no ACP support for worker profile {tool!r}", "use claude or kiro")
    npm = shutil.which("npm")
    if not npm:
        raise AosError("npm not found", "install Node.js (it brings npm), then re-run `aos acp install claude`")
    prefix = acp_home(home)
    prefix.mkdir(parents=True, exist_ok=True)
    pkg = f"{CLAUDE_ADAPTER_PACKAGE}@{CLAUDE_ADAPTER_VERSION}"
    r = run_command([npm, "install", "--prefix", str(prefix), "--no-save", pkg], capture_output=True, text=True)
    if r.returncode != 0:
        raise AosError(f"npm install {pkg} failed", (r.stderr or r.stdout or "").strip()[-500:])
    return f"installed {pkg} in {prefix}"


def status(tool: str, settings: dict, home: str | Path) -> tuple[bool, str]:
    """How board work with `tool` will run on this machine, for `aos doctor`."""
    if settings.get("transport", "acp") != "acp":
        return True, f"{tool}: cli (transport set to {settings.get('transport')})"
    try:
        argv, _ = launch(tool, settings, home)
    except AdapterMissing as e:
        return False, f"{tool} over ACP unavailable: {e.message}; falls back to the CLI"
    return True, f"{tool}: acp ({' '.join(argv)})"


def steer_request(tool: str, session_id: str, text: str) -> tuple[str, dict]:
    if tool == "kiro":
        return "_session/steer", {"sessionId": session_id, "message": text}
    return "_session/steering", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}],
                                 "_meta": {"steering": {"priority": "now", "idleBehavior": "promptRequired"}}}


def option_for(decision: str, options: list[dict]) -> str | None:
    """The optionId for allow / always / reject among an agent's permission options."""
    # "always" is still allow_once: aos records the rule itself; an agent's allow_always would
    # persist the agent's own rule (Claude: .claude/settings.local.json) and bypass aos's checks
    wanted = {"allow": ("allow_once",), "always": ("allow_once",),
              "reject": ("reject_once", "reject_always")}[decision]
    for kind in wanted:
        for o in options:
            if o.get("kind") == kind:
                return o.get("optionId")
    return None
