"""When does a board task need a human? Shared by `aos board watch` (Kiro watch nodes)
and the `board_wait` MCP tool, so both agree on what to report.

A task needs attention when it is blocked, failed, has a pending contract
proposal, or can never start because a dependency failed/was cancelled (stuck).
Each situation has a signature; callers report a task only when its signature
changed since they last reported it, so an unanswered question isn't repeated.
"""

from __future__ import annotations

from .board import Board

TERMINAL = ("done", "cancelled")


def task_attention(board: Board, t: dict, stuck: dict[int, list[int]] | None = None) -> tuple[str, dict]:
    """(signature, info). The signature is "" when nobody needs to look at the task."""
    events = [e for e in board.events(t["feature"]) if e["task"] == t["id"]]
    proposals = [{"id": p["id"], "reason": p["reason"], "change": p["body"]}
                 for p in board.proposals(t["feature"], "pending") if p["task"] == t["id"]]
    info = {"task": t["id"], "feature": t["feature"], "project": t["project"], "title": t["title"],
            "status": t["status"], "attempts": t["attempts"], "max_attempts": t["max_attempts"]}
    if t["status"] in TERMINAL:
        info["result"] = t["result"]
        return "", info
    sig = ""
    if t["status"] == "blocked":
        blk = [e for e in events if e["kind"] == "blocker"]
        info["blocker"] = blk[-1]["body"] if blk else ""
        sig = f"blocked:{blk[-1]['id'] if blk else 0}"
    elif t["status"] == "failed":
        st = [e for e in events if e["kind"] == "status"]
        info["last_error"] = st[-1]["body"] if st else ""
        sig = f"failed:{st[-1]['id'] if st else 0}"
    elif stuck and t["id"] in stuck:
        info["status"] = "stuck"
        info["waiting_on"] = stuck[t["id"]]
        sig = "stuck:" + ",".join(map(str, stuck[t["id"]]))
    if proposals:
        info["proposals"] = proposals
        sig += "|proposals:" + ",".join(str(p["id"]) for p in proposals)
    return sig, info
