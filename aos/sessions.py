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
MAX_HITS = 20              # per capture: the most recent corrections
SELF_FIX_LOOKAHEAD = 8     # turns after a failed command in which its fix must appear
BEFORE, AFTER = 3, 1
REFUSED = "The user doesn't want to proceed with this tool use"
# the first words of a user message that corrects the agent (English, Spanish). "para" (stop)
# only on its own: "para el login…" means "for the login…"
_CORRECTION = re.compile(
    r"(?i)^\s*(no\b|nope\b|don'?t\b|do not\b|stop\b|wrong\b|that'?s not\b|not like that\b|instead\b|"
    r"actually\b|revert\b|undo\b|eso no\b|mal\b|en vez de\b|en lugar de\b|deshaz\b|revierte\b|para\s*[.!,]*\s*$)")
_BARE_NO = re.compile(r"(?i)^\s*(no|nope)[.!]?\s*$")
_POLITE = re.compile(r"(?i)^\s*(no|nope)[\s,.!]*(thanks|thank you|all good|that'?s all|that'?s it|we'?re good)"
                     r"|^\s*actually[\s,.!]*(great|perfect|good|yes|fine|looks good|sounds good|go ahead)")


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
            _read_entry(json.loads(line), turns, tools)
        except (ValueError, TypeError, AttributeError, KeyError):
            continue  # one odd line never drops the rest of the transcript
    return turns


def _read_entry(e, turns: list[Turn], tools: dict) -> None:
    """One transcript entry → turns (tool results are matched to their calls)."""
    if not isinstance(e, dict) or e.get("type") not in ("user", "assistant") or e.get("isMeta") or e.get("isSidechain"):
        return
    content = (e.get("message") or {}).get("content")
    if e["type"] == "user" and isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out = _text(b.get("content"))
                name, inp = tools.get(b.get("tool_use_id"), (None, None))
                turns.append(Turn(len(turns), "tool", out, name, inp, bool(b.get("is_error")), REFUSED in out))
        return
    if e["type"] == "user":
        text = _text(content).strip()
        if text:
            turns.append(Turn(len(turns), "user", text))
        return
    for b in content if isinstance(content, list) else [{"type": "text", "text": _text(content)}]:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and (b.get("text") or "").strip():
            turns.append(Turn(len(turns), "assistant", b["text"].strip()))
        elif b.get("type") == "tool_use":
            inp = json.dumps(b.get("input") or {}, separators=(",", ":"), sort_keys=True)
            tools[b.get("id")] = (b.get("name"), inp)
            turns.append(Turn(len(turns), "assistant", "", b.get("name"), inp))


def _program(t: Turn) -> str:
    """The program and subcommand of a shell call ("npm test", "git commit")."""
    try:
        cmd = json.loads(t.input or "{}").get("command") or ""
    except ValueError:
        cmd = ""
    return " ".join(str(cmd).split()[:2])


def find_corrections(turns: list[Turn]) -> list[Hit]:
    hits: list[Hit] = []
    said = ""  # the agent's last words, for "no" answering its own question
    for i, t in enumerate(turns):
        if t.role == "assistant" and t.text:
            said = t.text
        if t.role == "user" and i and turns[i - 1].role in ("assistant", "tool") and _CORRECTION.match(t.text):
            if not (_BARE_NO.match(t.text) and said.rstrip().endswith("?")) and not _POLITE.match(t.text):
                hits.append(Hit(i, "correction"))
        elif t.role == "tool" and t.refused:
            hits.append(Hit(i, "refused_tool"))
        elif t.role == "tool" and t.error and t.tool == "Bash" and _program(t):
            later = [u for u in turns[i + 1: i + 1 + SELF_FIX_LOOKAHEAD] if u.role == "tool" and u.tool == "Bash"][:3]
            if any(u.input != t.input and _program(u) == _program(t) and not u.error for u in later):
                hits.append(Hit(i, "self_fix"))
    return hits[-MAX_HITS:]


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
        out.append({"id": n, "kind": h.kind, "turn": h.index,
                    "turns": [{"index": t.index, "role": t.role, "text": _show(t)} for t in window]})
    return out


def entry_excerpts(entry: dict) -> list[dict]:
    """The excerpts of a queued session: its transcript re-read, only the queued corrections."""
    turns = read_claude_transcript(entry["transcript"])
    keys = {h["key"] for h in entry.get("hits", [])}
    if entry.get("all_turns"):  # manual trigger: the whole (tail of the) session as one window
        return [{"id": 1, "kind": "manual", "turn": turns[-1].index if turns else 0,
                 "turns": [{"index": t.index, "role": t.role, "text": _show(t)} for t in turns[-40:]]}]
    hits = [h for h in find_corrections(turns) if hit_key(turns[h.index], h.kind) in keys]
    return excerpts(turns, hits)


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


def entry(data, entry_id: str) -> dict | None:
    return _load(_path(data, entry_id))


def entries(data, status: str | None = None) -> list[dict]:
    d = queue_dir(data)
    found = [e for p in sorted(d.glob("*.json")) if (e := _load(p))] if d.is_dir() else []
    return [e for e in found if status is None or e.get("status") == status]


def enqueue(data, tool: str, session: str, project: str, project_dir: str, transcript: str,
            hits: list[dict], all_turns: bool = False) -> dict | None:
    """Queue the hits not already queued for this session; None when nothing is new."""
    seen = {h["key"] for e in entries(data) if e.get("tool") == tool and e.get("session") == session
            for h in e.get("hits", [])}
    new = [h for h in hits if h["key"] not in seen]
    if not new:
        return None
    entry = {"id": f"{time.strftime('%Y%m%d-%H%M%S')}-{tool}-{_secrets.token_hex(3)}", "tool": tool,
             "session": session, "project": project, "project_dir": str(project_dir), "transcript": str(transcript),
             "hits": new, "status": "pending", "tries": 0, "reason": "", "created": _now(),
             **({"all_turns": True} if all_turns else {})}
    _save(data, entry)
    return entry


def _stale(e: dict, stale_after_s: float | None) -> bool:
    if e.get("status") != "running" or stale_after_s is None:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(e.get("updated") or e["created"])).total_seconds()
    except (ValueError, KeyError):
        return True
    return age > stale_after_s


def claim(data, entry_id: str, stale_after_s: float | None = None) -> dict | None:
    """pending → running, once: a second job gets None. A `running` entry older than
    `stale_after_s` lost its job (sleep, reboot, kill) and is taken over."""
    path = _path(data, entry_id)
    with locked(path):
        e = _load(path)
        if not e or not (e.get("status") == "pending" or _stale(e, stale_after_s)):
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


def release(data, entry_id: str, reason: str) -> None:
    """running → pending without spending a try (e.g. the daily budget is reached)."""
    path = _path(data, entry_id)
    with locked(path):
        e = _load(path)
        if e and e.get("status") == "running":
            e["status"], e["reason"] = "pending", reason
            _save(data, e)


def claude_project_folder(project_dir) -> str:
    """Claude Code keeps a project's transcripts in ~/.claude/projects/<the path, / and . as ->."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(Path(project_dir).expanduser().resolve()))


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


def _launch(entry_id: str, data=None) -> None:
    """Start the background learner for one entry, detached from the hook. It runs from the
    data dir, so a project's own `aos.py` can't shadow agenticOS on Python's path."""
    from .config import data_root
    data = Path(data or data_root())
    log = data / "logs" / "sessions.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as out:
        subprocess.Popen([sys.executable, "-m", "aos", "lessons", "learn-session", entry_id],
                         cwd=str(data), stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True,
                         env={**os.environ})


def capture(home, data, settings: dict, tool: str, transcript, session: str | None, project_dir,
            launch=None) -> str:
    """Called by the end-of-session hook: find corrections and queue them. Fast, never raises."""
    try:
        learning = settings.get("learning") or {}
        if not learning.get("enabled", True) or not learning.get("sessions", True):
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
        hits, keys = [], set()
        for h in find_corrections(turns):
            key = hit_key(turns[h.index], h.kind)
            if key not in keys:
                keys.add(key)
                hits.append({"key": key, "kind": h.kind})
        if not hits:
            return "no corrections"
        entry = enqueue(data, tool, session or Path(transcript).stem, slug, pdir, transcript, hits)
        if not entry:
            return "nothing new"
        if launch:
            launch(entry["id"])
        else:
            globals()["_launch"](entry["id"], data)
        return f"queued {entry['id']}"
    except Exception as e:  # the hook must never break or slow the user's session
        _log(data, f"capture failed: {type(e).__name__}: {e}")
        return "error"
