"""Learning from the user's own sessions (spec 2026-10-30, learning loop phase B).

Reads a finished session's transcript, finds the user's corrections with fixed rules (no AI,
no cost), and builds short, scrubbed, quoted excerpts around them for a session learner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .lessons import scrub_secrets
from .quoting import quoted

TURN_CHARS, TOOL_CHARS = 1500, 300
BEFORE, AFTER = 3, 1
REFUSED = "The user doesn't want to proceed with this tool use"
# the first words of a user message that corrects the agent (English, Spanish). "para" (stop)
# only on its own: "para el login…" means "for the login…"
_CORRECTION = re.compile(
    r"(?i)^\s*(no\b|nope\b|don'?t\b|do not\b|stop\b|wrong\b|that'?s not\b|not like that\b|instead\b|"
    r"actually\b|revert\b|undo\b|eso no\b|mal\b|en vez de\b|en lugar de\b|deshaz\b|revierte\b|para\s*[.!,]*\s*$)")
_BARE_NO = re.compile(r"(?i)^\s*(no|nope)[.!]?\s*$")


@dataclass
class Turn:
    index: int
    role: str                    # "user" | "assistant" | "tool"
    text: str
    tool: str | None = None      # tool name (assistant tool_use, and its result)
    input: str | None = None     # tool_use input, compact JSON
    error: bool = False          # tool_result is_error
    refused: bool = False        # the user refused this tool call


@dataclass
class Hit:
    index: int
    kind: str                    # "correction" | "refused_tool" | "self_fix"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(b.get("text") if b.get("type") == "text" else b.get("content", ""))
                         for b in content if isinstance(b, dict))
    return ""


def _tail_lines(path: Path, max_bytes: int) -> list[str]:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()                      # drop the partial first line
        return fh.read().decode("utf-8", errors="replace").splitlines()


def read_claude_transcript(path: str | Path, max_bytes: int = 4_000_000) -> list[Turn]:
    """Claude Code JSONL → turns. Only the tail is read; meta, sidechain and other entries are skipped."""
    turns: list[Turn] = []
    tools: dict[str, tuple[str, str]] = {}
    for line in _tail_lines(Path(path), max_bytes):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") not in ("user", "assistant") or e.get("isMeta") or e.get("isSidechain"):
            continue
        content = (e.get("message") or {}).get("content")
        if e["type"] == "user" and isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out = _text(b.get("content"))
                    name, inp = tools.get(b.get("tool_use_id"), (None, None))
                    turns.append(Turn(len(turns), "tool", out, name, inp, bool(b.get("is_error")), REFUSED in out))
            continue
        if e["type"] == "user":
            text = _text(content).strip()
            if text:
                turns.append(Turn(len(turns), "user", text))
            continue
        for b in content if isinstance(content, list) else [{"type": "text", "text": _text(content)}]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and (b.get("text") or "").strip():
                turns.append(Turn(len(turns), "assistant", b["text"].strip()))
            elif b.get("type") == "tool_use":
                inp = json.dumps(b.get("input") or {}, separators=(",", ":"), sort_keys=True)
                tools[b.get("id")] = (b.get("name"), inp)
                turns.append(Turn(len(turns), "assistant", "", b.get("name"), inp))
    return turns


def find_corrections(turns: list[Turn]) -> list[Hit]:
    hits: list[Hit] = []
    for i, t in enumerate(turns):
        if t.role == "user" and i and turns[i - 1].role in ("assistant", "tool") and _CORRECTION.match(t.text):
            said = next((p.text for p in reversed(turns[:i]) if p.role == "assistant" and p.text), "")
            if not (_BARE_NO.match(t.text) and said.rstrip().endswith("?")):
                hits.append(Hit(i, "correction"))
        elif t.role == "tool" and t.refused:
            hits.append(Hit(i, "refused_tool"))
        elif t.role == "tool" and t.error and t.tool:
            later = [u for u in turns[i + 1:] if u.role == "tool" and u.tool][:3]
            if any(u.tool == t.tool and u.input != t.input and not u.error for u in later):
                hits.append(Hit(i, "self_fix"))
    return hits


def _show(t: Turn) -> str:
    if t.role == "tool":
        return quoted(scrub_secrets(t.text), TOOL_CHARS)
    if t.role == "assistant" and t.tool:
        return quoted(scrub_secrets(f"[{t.tool}] {t.input}"), TOOL_CHARS)
    return quoted(scrub_secrets(t.text), TURN_CHARS)


def excerpts(turns: list[Turn], hits: list[Hit]) -> list[dict]:
    """For each hit: the turns before and after it, scrubbed, clipped and quoted."""
    out = []
    for n, h in enumerate(hits, 1):
        window = turns[max(0, h.index - BEFORE): h.index + AFTER + 1]
        out.append({"id": n, "kind": h.kind, "turns": [{"index": t.index, "role": t.role, "text": _show(t)}
                                                       for t in window]})
    return out
