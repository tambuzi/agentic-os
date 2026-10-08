"""Decide an ACP `session/request_permission` against a worker's neutral allow-list.

  allow  - covered by the allow-list (read / write inside the worktree / shell:<prefix> /
           mcp:<server>) or harmless (think, plan);
  reject - never acceptable for a board worker (git push, sudo, rm -rf /, edits outside its
           worktree);
  ask    - everything else: the board records a pending permission and asks the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_CHAINING = re.compile(r"[;&|`$()<>\\\r\n]")
_DANGEROUS = (re.compile(r"(^|\s)git\s+push(\s|$)"), re.compile(r"(^|\s)sudo(\s|$)"),
              re.compile(r"rm\s+-[a-z]*r[a-z]*f?[a-z]*\s+/(\s|$)"), re.compile(r"rm\s+-[a-z]*f[a-z]*r[a-z]*\s+/(\s|$)"))
_HARMLESS_KINDS = ("think", "switch_mode", "plan")


@dataclass
class Decision:
    decision: str        # allow | reject | ask
    summary: str         # what the agent wants, for the user and the timeline
    rule: str | None = None  # neutral allow-list rule that would cover it ("always")


def _tool_name(call: dict) -> str:
    meta = (call.get("_meta") or {}).get("claudeCode") or {}
    return str(meta.get("toolName") or call.get("title") or "")


def _raw_command(call: dict) -> str | None:
    raw = call.get("rawInput") or {}
    cmd = raw.get("command") or raw.get("cmd")
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    return str(cmd) if cmd else None


def _command(call: dict) -> str | None:
    """The command with whitespace normalised, for prefix matching only (never for the
    chaining check: a newline is a second command)."""
    cmd = _raw_command(call)
    return " ".join(cmd.split()) if cmd else None


def _paths(call: dict) -> list[str]:
    raw = call.get("rawInput") or {}
    paths = [loc.get("path") for loc in call.get("locations") or [] if loc.get("path")]
    for key in ("file_path", "path", "notebook_path"):
        if isinstance(raw.get(key), str):
            paths.append(raw[key])
    return paths


def _inside(path: str, worktree: Path) -> bool:
    if path.startswith("~") or "$" in path:  # the agent's shell/tool may expand these: treat as outside
        return False
    root = Path(worktree).resolve()
    p = Path(path)
    p = (root / p if not p.is_absolute() else p).resolve()
    return p == root or root in p.parents


def _mcp_server(name: str) -> tuple[str, str] | None:
    m = re.match(r"^mcp__([A-Za-z0-9_-]+?)__(.+)$", name)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^@?([A-Za-z0-9_-]+)/(.+)$", name)  # kiro spells MCP tools @server/tool
    return (m.group(1), m.group(2)) if m and not name.startswith("/") else None


def decide(call: dict, allowed: list[str], worktree: str | Path) -> Decision:
    kind = str(call.get("kind") or "other")
    name = _tool_name(call)

    if kind in _HARMLESS_KINDS:
        return Decision("allow", f"{kind}")

    mcp = _mcp_server(name)
    if mcp:
        server, tool = mcp
        ok = f"mcp:{server}" in allowed
        return Decision("allow" if ok else "ask", f"use MCP tool {server}/{tool}", f"mcp:{server}")

    if kind == "execute" or _command(call):
        cmd = _command(call) or name
        if any(p.search(cmd) for p in _DANGEROUS):
            return Decision("reject", f"run `{cmd}` (never allowed for board workers)")
        head = " ".join(cmd.split()[:2])
        rule = f"shell:{head}" if head else None
        if _CHAINING.search(_raw_command(call) or cmd):
            return Decision("ask", f"run `{cmd}` (chained or redirected command)", rule)
        for entry in allowed:
            if entry.startswith("shell:"):
                prefix = entry[6:]
                if cmd == prefix or cmd.startswith(prefix + " "):
                    return Decision("allow", f"run `{cmd}`")
        return Decision("ask", f"run `{cmd}`", rule)

    paths = _paths(call)
    if kind in ("edit", "delete", "move"):
        outside = [p for p in paths if not _inside(p, worktree)]
        if outside:
            return Decision("reject", f"{kind} {outside[0]} (outside its worktree)")
        if not paths:
            return Decision("ask", f"{kind} {name or 'files'} (no path given)")
        if "write" in allowed:
            return Decision("allow", f"{kind} {', '.join(paths[:3])}")
        return Decision("ask", f"{kind} {', '.join(paths[:3])}", "write")

    if kind in ("read", "search"):
        outside = [p for p in paths if not _inside(p, worktree)]
        if outside:
            return Decision("ask", f"read {outside[0]} (outside its worktree)")
        if "read" in allowed:
            return Decision("allow", f"{kind} {', '.join(paths[:3]) or name}")
        return Decision("ask", f"{kind} {', '.join(paths[:3]) or name}", "read")

    if kind == "fetch":
        return Decision("ask", f"fetch {name or (call.get('rawInput') or {}).get('url', '')}")

    return Decision("ask", f"{kind}: {name or 'unknown tool'}")
