"""One live ACP session for a board worker or reviewer, on top of AcpConnection."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from queue import Empty

from . import tools
from .rpc import AcpConnection, AcpError, Pending

CLIENT_CAPABILITIES = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}


class AcpWorkerSession:
    def __init__(self, conn: AcpConnection, tool: str):
        self.conn = conn
        self.tool = tool
        self.facts = tools.FACTS.get(tool, tools.FACTS["claude"])
        self.session_id: str | None = None
        self.capabilities: dict = {}
        self.cost = 0.0
        self.cost_unit = self.facts["cost_unit"]
        self.hermetic = True
        self.resumed = False
        self.turn: Pending | None = None
        self.last_prompt: str | None = None
        self._context_prefix: str | None = None
        self._turn_end = threading.Event()

    # -- setup -------------------------------------------------------------------
    def start(self, cwd: str | Path, mcp_servers: dict, context: str, resume_id: str | None = None,
              kiro_agent: str | None = None, timeout: float = 120, model: str | None = None) -> str:
        init = self.conn.request("initialize", {"protocolVersion": 1, "clientCapabilities": CLIENT_CAPABILITIES},
                                 timeout=timeout)
        self.capabilities = (init or {}).get("agentCapabilities") or {}
        params = {"cwd": str(cwd), "mcpServers": [
            {"name": name, "command": s["command"], "args": [str(a) for a in s.get("args") or []],
             "env": [{"name": k, "value": str(v)} for k, v in (s.get("env") or {}).items()]}
            for name, s in mcp_servers.items()]}
        if self.facts["context"] == "system_prompt":
            params["_meta"] = {"systemPrompt": {"append": context}}
            if model:
                params["_meta"]["claudeCode"] = {"options": {"model": model}}
        result = None
        if resume_id and self.capabilities.get("loadSession"):
            try:
                result = self.conn.request("session/load", {**params, "sessionId": resume_id}, timeout=timeout) or {}
                self.session_id, self.resumed = resume_id, True
            except AcpError:
                result = None  # couldn't resume: start fresh (the caller adds the resume hint)
        if result is None:
            result = self.conn.request("session/new", params, timeout=timeout) or {}
            self.session_id = result.get("sessionId")
        if self.facts["context"] == "agent_mode":
            modes = [m.get("id") for m in ((result.get("modes") or {}).get("availableModes") or [])]
            if kiro_agent and kiro_agent in modes:
                self.conn.request("session/set_mode", {"sessionId": self.session_id, "modeId": kiro_agent})
            else:
                self.hermetic = False
                self._context_prefix = context  # not isolated: at least give it the context
        return self.session_id

    # -- turns -------------------------------------------------------------------
    def prompt(self, text: str) -> Pending:
        if self._context_prefix:
            text, self._context_prefix = f"{self._context_prefix}\n\n{text}", None
        self._turn_end.clear()
        self.last_prompt = text
        self.turn = self.conn.request_async("session/prompt", {"sessionId": self.session_id,
                                                               "prompt": [{"type": "text", "text": text}]})
        return self.turn

    def steer(self, text: str) -> str | None:
        """'injected' (Claude, mid-turn), 'queued' (Kiro, after the running tool) or None
        (not delivered; with no turn running, Claude answers promptRequired instead of
        starting a turn of its own)."""
        method, params = tools.steer_request(self.tool, self.session_id, text)
        try:
            result = self.conn.request(method, params, timeout=30) or {}
        except AcpError:
            return None
        if result.get("queued"):
            return "queued"
        outcome = result.get("outcome") or "delivered"
        return None if outcome == "promptRequired" else outcome

    def turn_outcome(self) -> tuple[str | None, str | None]:
        """(stopReason, error message) of the finished turn."""
        try:
            return (self.turn.result() or {}).get("stopReason"), None
        except AcpError as e:
            return None, e.message

    def cancel(self) -> None:
        try:
            self.conn.notify("session/cancel", {"sessionId": self.session_id})
        except AcpError:
            pass

    def wait_turn_end(self, timeout: float = 5.0) -> bool:
        """Kiro answers a prompt (even `cancelled`) before its turn has really ended; the
        trailing `_kiro.dev/metadata` marks it. Other tools: the prompt response is enough."""
        if not self.facts["turn_end"]:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain()
            if self._turn_end.is_set():
                return True
            time.sleep(0.05)
        return False

    # -- events ------------------------------------------------------------------
    def drain(self, on_request=None) -> None:
        """Process queued notifications (cost, turn end). Agent->client requests go to
        `on_request(req_id, method, params)`; without a handler they get method-not-found."""
        while True:
            try:
                item = self.conn.events.get_nowait()
            except Empty:
                return
            if item[0] == "request":
                _, req_id, method, params = item
                if on_request:
                    on_request(req_id, method, params)
                else:
                    self.conn.respond(req_id, error={"code": -32601, "message": f"client does not implement {method}"})
                continue
            _, method, params = item
            if method == "session/update":
                cost = ((params.get("update") or {}).get("cost") or {}).get("amount")
                if isinstance(cost, (int, float)):  # Claude: total_cost_usd, a running total for the session
                    self.cost = max(self.cost, float(cost))
            elif method == self.facts["turn_end"]:
                for m in params.get("meteringUsage") or []:
                    if isinstance(m.get("value"), (int, float)):
                        self.cost += float(m["value"])
                self._turn_end.set()

    def close(self) -> None:
        self.conn.close()
