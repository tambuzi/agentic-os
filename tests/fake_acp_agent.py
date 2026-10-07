"""Fake ACP agent for tests: newline-delimited JSON-RPC 2.0 on stdio.

Behaviour comes from the environment:
  FAKE_ACP_TOOL   claude (default) | kiro   -- which tool's protocol details to mimic
  FAKE_ACP_MODE   (or FAKE_ACP_MODE_<task>) what a prompt does:
      echo         reply with the prompt text, end_turn                       (rpc tests)
      complete     commit a file in cwd, complete the board task -> review   (dispatcher tests)
      review_pass  (reviewer) review_verdict pass
      review_fail  (reviewer) review_verdict fail
      permission   ask session/request_permission for `npm publish`, record the answer, then complete
      silent       never answer the prompt and send nothing (stall)
      no_verdict   end the turn without touching the board (twice -> nudge then failure)
      slow         stream an update every 0.2 s for FAKE_ACP_SLOW seconds, then complete
  FAKE_ACP_COST   cost to report per turn (USD for claude, credits for kiro)
  FAKE_ACP_LOG    file where the agent appends what it saw (steers, permission outcomes, loads)
The board task and generation are read from the aos MCP server args given in session/new.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

TOOL = os.environ.get("FAKE_ACP_TOOL", "claude")
LOG = os.environ.get("FAKE_ACP_LOG")
lock = threading.Lock()
state = {"task": None, "generation": None, "role": "worker", "sessions": set(), "cancel": threading.Event(),
         "pending_perm": {}, "next_id": 1000, "prompts": 0}
sys.stderr.write("fake acp agent started\n")
sys.stderr.flush()


def send(msg):
    with lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def note(kind, data):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"kind": kind, "data": data}) + "\n")


def update(sid, upd):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": upd}})


def mode():
    t = state["task"]
    if state["role"] == "reviewer":
        return "review_" + os.environ.get("FAKE_ACP_REVIEW", "pass")
    return os.environ.get(f"FAKE_ACP_MODE_{t}", os.environ.get("FAKE_ACP_MODE", "echo"))


def board():
    from aos.board import Board
    from aos.config import data_root
    return Board(data_root())


def finish_turn(rid, sid, reason="end_turn"):
    cost = os.environ.get("FAKE_ACP_COST")
    if cost and TOOL == "claude":
        update(sid, {"sessionUpdate": "usage_update", "used": 100, "size": 200000,
                     "cost": {"amount": float(cost), "currency": "USD"}})
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": reason}})
    if TOOL == "kiro":  # kiro: the turn has really ended only with this trailing notification
        time.sleep(0.3)
        send({"jsonrpc": "2.0", "method": "_kiro.dev/metadata", "params": {"sessionId": sid,
              "meteringUsage": [{"value": float(cost or 0.05), "unit": "credit"}]}})


def complete_task(summary="done by fake acp agent"):
    if state["task"] is None:
        return
    cwd = Path.cwd()
    (cwd / f"acp-{state['task']}-{time.time_ns()}.txt").write_text("work\n")
    subprocess.run(["git", "add", "."], check=False, capture_output=True)
    subprocess.run(["git", "commit", "-qm", f"acp task {state['task']}"], check=False, capture_output=True)
    b = board()
    b.mark_seen(state["task"])
    b.complete(state["task"], summary, author=f"task:{state['task']}", generation=state["generation"], to="review")


def ask_permission(sid):
    state["next_id"] += 1
    pid = state["next_id"]
    done = threading.Event()
    state["pending_perm"][pid] = {"event": done, "answer": None}
    options = ([{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                {"optionId": "always", "name": "Always", "kind": "allow_always"},
                {"optionId": "reject", "name": "Reject", "kind": "reject_once"}])
    send({"jsonrpc": "2.0", "id": pid, "method": "session/request_permission", "params": {
        "sessionId": sid, "options": options,
        "toolCall": {"toolCallId": "t1", "title": "npm publish", "kind": "execute",
                     "rawInput": {"command": "npm publish"}}}})
    done.wait(timeout=60)
    return state["pending_perm"][pid]["answer"]


def run_prompt(rid, sid, text):
    state["prompts"] += 1
    state["cancel"].clear()
    m = mode()
    if m == "echo":
        update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})
        finish_turn(rid, sid)
    elif m == "silent":
        state["cancel"].wait(timeout=600)
        finish_turn(rid, sid, "cancelled")
    elif m == "no_verdict_once":  # forgets task_complete on the first turn, finishes after a nudge
        if state["prompts"] == 1:
            update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done?"}})
        else:
            complete_task("finished after the nudge")
        finish_turn(rid, sid)
    elif m == "no_verdict":
        update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "I think I'm done"}})
        finish_turn(rid, sid)
    elif m == "slow":
        end = time.monotonic() + float(os.environ.get("FAKE_ACP_SLOW", "3"))
        while time.monotonic() < end and not state["cancel"].is_set():
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t", "status": "in_progress"})
            time.sleep(0.2)
        if state["cancel"].is_set():
            finish_turn(rid, sid, "cancelled")
            return
        complete_task()
        finish_turn(rid, sid)
    elif m == "permission":
        answer = ask_permission(sid)
        note("permission_outcome", answer)
        complete_task(f"permission answer: {answer}")
        finish_turn(rid, sid)
    elif m in ("review_pass", "review_fail"):
        board().review_verdict(state["task"], m == "review_pass", "fake acp review", generation=state["generation"])
        finish_turn(rid, sid)
    else:  # complete
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "edit", "kind": "edit"})
        complete_task()
        finish_turn(rid, sid)


def handle(msg):
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:  # a response to our request (permission)
        pending = state["pending_perm"].get(rid)
        if pending:
            outcome = (msg.get("result") or {}).get("outcome") or {}
            pending["answer"] = outcome.get("optionId") or outcome.get("outcome")
            pending["event"].set()
        return
    if method == "initialize":
        meta = {"steering": {"supported": True}} if TOOL == "claude" else {}
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {
            "loadSession": True}, "_meta": meta}})
    elif method in ("session/new", "session/load"):
        args = []
        for s in params.get("mcpServers") or []:
            if s.get("name") == "aos":
                args = s.get("args") or []
        if "--task" in args:
            state["task"] = int(args[args.index("--task") + 1])
        if "--attempt" in args:
            state["generation"] = int(args[args.index("--attempt") + 1])
        state["role"] = "reviewer" if "--review" in args else "worker"
        sid = params.get("sessionId") or f"sess-{state['task']}-{time.time_ns()}"
        state["sessions"].add(sid)
        note(method, {"sessionId": sid, "meta": params.get("_meta"), "cwd": params.get("cwd"),
                      "mcp": [s.get("name") for s in params.get("mcpServers") or []]})
        modes = {"currentModeId": "default", "availableModes": [{"id": "default", "name": "Default"}]}
        if TOOL == "kiro":
            agents = [p.stem for p in Path(os.environ.get("FAKE_KIRO_AGENTS", "/nonexistent")).glob("*.json")]
            modes = {"currentModeId": "kiro_default",
                     "availableModes": [{"id": a, "name": a} for a in ["kiro_default", *agents]]}
        result = {"modes": modes} if method == "session/load" else {"sessionId": sid, "modes": modes}
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif method == "session/set_mode":
        note("set_mode", params.get("modeId"))
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "session/prompt":
        text = " ".join(b.get("text", "") for b in params.get("prompt") or [])
        note("prompt", text[:300])
        threading.Thread(target=run_prompt, args=(rid, params["sessionId"], text), daemon=True).start()
    elif method == "session/cancel":
        note("cancel", True)
        state["cancel"].set()
    elif method == "_session/steering" and TOOL == "claude":
        note("steer", " ".join(b.get("text", "") for b in params.get("prompt") or []))
        send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": "injected"}})
    elif method == "_session/steer" and TOOL == "kiro":
        if not isinstance(params.get("message"), str):
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "message must be a string"}})
            return
        note("steer", params["message"])
        send({"jsonrpc": "2.0", "id": rid, "result": {"queued": True}})
    elif method == "test/garbage":
        with lock:
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
        send({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}})
    elif method == "test/ask_client":
        send({"jsonrpc": "2.0", "id": 777, "method": "fs/read_text_file", "params": {"path": "/etc/hosts"}})
        send({"jsonrpc": "2.0", "id": rid, "result": {"asked": True}})
    elif method == "test/exit":
        os._exit(3)
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}})


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        handle(json.loads(line))
    except json.JSONDecodeError:
        continue
