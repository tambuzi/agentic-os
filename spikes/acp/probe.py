"""THROWAWAY ACP spike: drive an ACP agent over stdio and record what works.

Usage:
  python probe.py claude <path/to/claude-agent-acp/dist/index.js> <git-workdir> <aos-bin>
  python probe.py kiro   kiro-cli                                 <git-workdir> <aos-bin>
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from queue import Empty, Queue

TOOL, ENTRY, WORKDIR, AOS_BIN = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
COMMAND = ["node", ENTRY] if TOOL == "claude" else [ENTRY, "acp"]
RESULTS: dict[str, object] = {}


class Client:
    def __init__(self):
        env = {**os.environ}
        if TOOL == "claude":
            env["CLAUDE_CODE_EXECUTABLE"] = shutil.which("claude")
        self.proc = subprocess.Popen(COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=open(os.path.join(WORKDIR, "..", "adapter.stderr"), "ab"),
                                     text=True, bufsize=1, env=env, cwd=WORKDIR, start_new_session=True)
        self.next_id = 0
        self.responses: dict[int, Queue] = {}
        self.updates: list[dict] = []
        self.requests: list[dict] = []
        self.permission_choice = "allow_once"
        self.last_event = time.monotonic()
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.last_event = time.monotonic()
            if "method" in msg and "id" in msg:            # agent -> client request
                self.requests.append(msg)
                self._answer(msg)
            elif "method" in msg:                           # notification
                if msg["method"] == "session/update":
                    self.updates.append(msg["params"]["update"])
            elif "id" in msg:                               # response to us
                q = self.responses.get(msg["id"])
                if q:
                    q.put(msg)

    def _answer(self, req):
        if req["method"] == "session/request_permission":
            options = req["params"]["options"]
            pick = next((o for o in options if o.get("kind") == self.permission_choice), options[0])
            self._send({"jsonrpc": "2.0", "id": req["id"],
                        "result": {"outcome": {"outcome": "selected", "optionId": pick["optionId"]}}})
        else:
            self._send({"jsonrpc": "2.0", "id": req["id"],
                        "error": {"code": -32601, "message": f"client does not implement {req['method']}"}})

    def call(self, method, params, timeout=240):
        self.next_id += 1
        rid = self.next_id
        q = Queue()
        self.responses[rid] = q
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return q.get(timeout=timeout)
        except Empty:
            return {"error": {"message": f"timeout after {timeout}s"}}

    def call_async(self, method, params):
        self.next_id += 1
        rid = self.next_id
        q = Queue()
        self.responses[rid] = q
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return q

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def rss_mb(self):
        out = subprocess.run(["ps", "-o", "rss=", "-g", str(self.proc.pid)], capture_output=True, text=True).stdout
        return sum(int(x) for x in out.split() if x.strip().isdigit()) / 1024

    def close(self):
        try:
            os.killpg(self.proc.pid, 15)
        except ProcessLookupError:
            pass


def kinds(updates):
    out = {}
    for u in updates:
        k = u.get("sessionUpdate", "?")
        out[k] = out.get(k, 0) + 1
    return out


c = Client()
init = c.call("initialize", {"protocolVersion": 1, "clientCapabilities": {
    "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
RESULTS["1_initialize"] = init.get("result") or init.get("error")

mcp = [{"name": "aos", "command": AOS_BIN, "args": ["serve", "--project", WORKDIR], "env": []}]
new = c.call("session/new", {"cwd": WORKDIR, "mcpServers": mcp})
sid = (new.get("result") or {}).get("sessionId")
RESULTS["2_session_new"] = {"sessionId": sid, "error": new.get("error"),
                            "modes": (new.get("result") or {}).get("modes"),
                            "models": [m.get("modelId") for m in ((new.get("result") or {}).get("models") or {}).get("availableModels", [])][:6]}

# 3. a prompt that needs a write; permission requests answered "allow once"
peak = 0.0
t0 = time.monotonic()
q = c.call_async("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text":
    "Create a file named hello.txt containing exactly the word hi. Then call the aos MCP tool memory_read "
    "with scope user and say how many characters the cap is. Be brief."}]})
resp = None
while resp is None and time.monotonic() - t0 < 240:
    try:
        resp = q.get(timeout=1)
    except Empty:
        peak = max(peak, c.rss_mb())
RESULTS["3_prompt_with_permission"] = {
    "stopReason": ((resp or {}).get("result") or {}).get("stopReason"), "error": (resp or {}).get("error"),
    "seconds": round(time.monotonic() - t0, 1), "update_kinds": kinds(c.updates),
    "permission_requests": len([r for r in c.requests if r["method"] == "session/request_permission"]),
    "other_agent_requests": sorted({r["method"] for r in c.requests if r["method"] != "session/request_permission"}),
    "hello_txt": open(os.path.join(WORKDIR, "hello.txt")).read().strip() if os.path.exists(os.path.join(WORKDIR, "hello.txt")) else None,
    "aos_tool_called": any("memory_read" in json.dumps(u) for u in c.updates),
    "peak_rss_mb": round(peak, 0)}

# 4. cancel mid-turn
c.updates.clear()
t0 = time.monotonic()
q = c.call_async("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text":
    "Run the shell command `for i in $(seq 1 30); do echo $i; sleep 1; done` and report the output."}]})
time.sleep(8)
c.notify("session/cancel", {"sessionId": sid})
try:
    resp = q.get(timeout=60)
except Empty:
    resp = None
RESULTS["4_cancel"] = {"stopReason": ((resp or {}).get("result") or {}).get("stopReason"),
                       "seconds_until_stopped": round(time.monotonic() - t0, 1)}
time.sleep(3)  # kiro answers `cancelled` before teardown ends; a prompt sent at once gets `refusal`

# 5. mid-turn steering: start a 30 s command, steer after 6 s, see if the same turn changes course
c.updates.clear()
t0 = time.monotonic()
q = c.call_async("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text":
    "Run the shell command `for i in $(seq 1 30); do echo $i; sleep 1; done` and then summarise the output."}]})
time.sleep(6)
results = {}
STEER = "Change of plan from the user: stop that command now, do not run it again, and reply with exactly STEERED."
if TOOL == "claude":   # claude-agent-acp: prompt blocks + priority; injected mid-turn
    method, params = "_session/steering", {"sessionId": sid, "prompt": [{"type": "text", "text": STEER}],
                                           "_meta": {"steering": {"priority": "now"}}}
else:                  # kiro-cli: `message` string; queued until the running tool finishes
    method, params = "_session/steer", {"sessionId": sid, "message": STEER}
r = c.call(method, params, timeout=30)
results[method] = r.get("result", r.get("error"))
try:
    resp = q.get(timeout=120)
except Empty:
    resp = {}
text = "".join(u.get("content", {}).get("text", "") for u in c.updates if u.get("sessionUpdate") == "agent_message_chunk")
RESULTS["5_mid_turn_steer"] = {"methods": results, "stopReason": (resp.get("result") or {}).get("stopReason"),
                               "seconds": round(time.monotonic() - t0, 1), "final_text_tail": text[-80:]}

# 6. resume the session in a brand-new adapter process
c.close()
time.sleep(1)
c2 = Client()
c2.call("initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}}})
load = c2.call("session/load", {"sessionId": sid, "cwd": WORKDIR, "mcpServers": mcp}, timeout=120)
remember = None
if "result" in load:
    c2.updates.clear()
    r = c2.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text":
        "In one short sentence: what file did you create earlier in this session?"}]})
    remember = "".join(u.get("content", {}).get("text", "") for u in c2.updates
                       if u.get("sessionUpdate") == "agent_message_chunk")[:200]
RESULTS["6_resume"] = {"load": load.get("error") or "ok", "answer_after_resume": remember}
c2.close()

print(json.dumps(RESULTS, indent=2, default=str))
