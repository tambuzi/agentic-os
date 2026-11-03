"""Skill health (spec 2026-11-01): how the skills agents use are doing in real sessions.

Every finished session's skill uses are recorded by the end-of-session capture (no AI, no
transcript text): which skill, and whether the user corrected the agent soon after it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .store import locked

MAX_USES = 50      # per capture
WINDOW = 15        # turns after a skill use in which a correction counts against it


def _runs_file(data) -> Path:
    return Path(data) / "skills" / "runs.jsonl"


def skill_uses(turns, hits) -> list[dict]:
    """Each Skill tool call in the transcript, with its outcome."""
    uses = [t for t in turns if t.role == "assistant" and t.tool == "Skill"][-MAX_USES:]
    corrections = sorted(h.index for h in hits if h.kind == "correction")
    out = []
    for k, u in enumerate(uses):
        try:
            name = str(json.loads(u.input or "{}").get("skill") or "").strip()
        except ValueError:
            name = ""
        if not name:
            continue
        nxt = uses[k + 1].index if k + 1 < len(uses) else float("inf")
        corrected = any(u.index < c < nxt and c <= u.index + WINDOW for c in corrections)
        before = next((p.text for p in reversed(turns[:u.index]) if p.role == "user"), "")
        out.append({"skill": name, "outcome": "corrected" if corrected else "ok", "_anchor": before, "_input": u.input})
    return out


def _key(session: str, use: dict) -> str:
    return hashlib.sha1(f"{session}\0{use['_anchor']}\0{use['_input']}".encode()).hexdigest()[:16]


def record_uses(data, tool: str, project: str, session: str, uses: list[dict]) -> int:
    """Append the uses not recorded yet for this session; returns how many were added."""
    path = _runs_file(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        seen = {r.get("key") for r in read_uses(data) if r.get("session") == session}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new = []
        for u in uses:
            k = _key(session, u)
            if k not in seen:
                seen.add(k)
                new.append({"skill": u["skill"], "tool": tool, "project": project, "session": session,
                            "key": k, "outcome": u["outcome"], "at": now})
        if new:
            with open(path, "a") as fh:
                for r in new:
                    fh.write(json.dumps(r) + "\n")
        return len(new)


def read_uses(data, days: int | None = None, today: date | None = None) -> list[dict]:
    path = _runs_file(data)
    if not path.exists():
        return []
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat() if days else ""
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("skill") and r.get("at", "")[:10] >= cutoff:
            out.append(r)
    return out
