"""Learning from the user's own sessions (spec 2026-10-30, learning loop phase B).

Reads a finished session's transcript, finds the user's corrections with fixed rules (no AI,
no cost), and builds short, scrubbed, quoted excerpts around them for a session learner.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets as _secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .lessons import scrub_secrets
from .quoting import quoted
from .store import locked, read_text, write_atomic

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


# -- the queue ---------------------------------------------------------------------

MAX_TRIES = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_dir(data: str | Path) -> Path:
    return Path(data) / "sessions" / "queue"


def _path(data, entry_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entry_id or ""):
        raise ValueError(f"bad queue id {entry_id!r}")
    return queue_dir(data) / f"{entry_id}.json"


def _load(path: Path) -> dict | None:
    try:
        e = json.loads(read_text(path))
        return e if isinstance(e, dict) else None
    except (OSError, ValueError):
        return None


def _save(data, entry: dict) -> None:
    entry["updated"] = _now()
    write_atomic(_path(data, entry["id"]), json.dumps(entry, indent=1) + "\n")


def hit_key(turn: Turn, kind: str) -> str:
    """A stable identity for a correction (positions shift as a transcript grows)."""
    return hashlib.sha1(f"{kind}\0{turn.role}\0{turn.text}\0{turn.input}".encode()).hexdigest()[:16]


def entries(data, status: str | None = None) -> list[dict]:
    d = queue_dir(data)
    found = [e for p in sorted(d.glob("*.json")) if (e := _load(p))] if d.is_dir() else []
    return [e for e in found if status is None or e.get("status") == status]


def enqueue(data, tool: str, session: str, project: str, project_dir: str, transcript: str,
            hits: list[dict]) -> dict | None:
    """Queue the hits not already queued for this session; None when nothing is new."""
    seen = {h["key"] for e in entries(data) if e.get("tool") == tool and e.get("session") == session
            for h in e.get("hits", [])}
    new = [h for h in hits if h["key"] not in seen]
    if not new:
        return None
    entry = {"id": f"{time.strftime('%Y%m%d-%H%M%S')}-{tool}-{_secrets.token_hex(3)}", "tool": tool,
             "session": session, "project": project, "project_dir": str(project_dir), "transcript": str(transcript),
             "hits": new, "status": "pending", "tries": 0, "reason": "", "created": _now()}
    _save(data, entry)
    return entry


def claim(data, entry_id: str) -> dict | None:
    """pending → running, once: a second job gets None."""
    path = _path(data, entry_id)
    with locked(path):
        e = _load(path)
        if not e or e.get("status") != "pending":
            return None
        e["status"] = "running"
        _save(data, e)
        return e


def finish(data, entry_id: str, ok: bool, reason: str = "") -> None:
    path = _path(data, entry_id)
    with locked(path):
        e = _load(path)
        if not e:
            return
        if ok:
            e["status"], e["reason"] = "done", reason
        else:
            e["tries"] = int(e.get("tries", 0)) + 1
            e["status"] = "failed" if e["tries"] >= MAX_TRIES else "pending"
            e["reason"] = reason
        _save(data, e)


def prune(data, days: int = 7) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for e in entries(data):
        if e.get("status") in ("done", "failed") and datetime.fromisoformat(e.get("updated", e["created"])) <= cutoff:
            _path(data, e["id"]).unlink(missing_ok=True)
            n += 1
    return n


# -- capture (the end-of-session hook) -----------------------------------------------
def _log(data, msg: str) -> None:
    try:
        p = Path(data) / "logs" / "sessions.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as fh:
            fh.write(f"{_now()} {msg}\n")
    except OSError:
        pass


def _launch(entry_id: str) -> None:
    """Start the background learner for one entry, detached from the hook."""
    from .config import aos_home
    log = Path(aos_home()) / "data" / "logs" / "sessions.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as out:
        subprocess.Popen([sys.executable, "-m", "aos", "lessons", "learn-session", entry_id],
                         stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True,
                         env={**os.environ})


def capture(home, data, settings: dict, tool: str, transcript, session: str | None, project_dir,
            launch=None) -> str:
    """Called by the end-of-session hook: find corrections and queue them. Fast, never raises."""
    try:
        learning = settings.get("learning") or {}
        if not learning.get("sessions", True):
            return "off"
        pdir = Path(project_dir or ".").expanduser().resolve()
        for automated in (Path(home) / "worktrees", Path(data) / "runs"):
            if pdir == automated.resolve() or automated.resolve() in pdir.parents:
                return "automated session"
        from .config import slug_for_path
        slug = slug_for_path(pdir)
        if not slug:
            return "not a linked project"
        if slug in (learning.get("sessions_exclude") or []):
            return "excluded"
        if tool != "claude":
            return f"{tool} capture not available yet"
        turns = read_claude_transcript(transcript)
        hits = [{"key": hit_key(turns[h.index], h.kind), "kind": h.kind} for h in find_corrections(turns)]
        if not hits:
            return "no corrections"
        entry = enqueue(data, tool, session or Path(transcript).stem, slug, pdir, transcript, hits)
        if not entry:
            return "nothing new"
        (launch or globals()["_launch"])(entry["id"])
        return f"queued {entry['id']}"
    except Exception as e:  # the hook must never break or slow the user's session
        _log(data, f"capture failed: {type(e).__name__}: {e}")
        return "error"
