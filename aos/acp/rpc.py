"""JSON-RPC 2.0 over an agent's stdio, newline-delimited (the ACP wire format).

A reader thread parses lines and either resolves a pending request or puts the message
on `events` for the owner to drain. It never touches the board: all board writes happen
on the dispatcher's thread.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from queue import Queue


class AcpError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(f"{message} ({code})")
        self.code = code
        self.message = message
        self.data = data


class Pending:
    """A request whose response hasn't necessarily arrived yet."""

    def __init__(self, method: str):
        self.method = method
        self._done = threading.Event()
        self._result = None
        self._error: AcpError | None = None

    def _resolve(self, result=None, error: AcpError | None = None) -> None:
        self._result, self._error = result, error
        self._done.set()

    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def result(self):
        if not self._done.is_set():
            raise AcpError(-32000, f"{self.method}: no response yet")
        if self._error:
            raise self._error
        return self._result


class AcpConnection:
    def __init__(self, argv: list[str], env: dict | None = None, cwd: str | Path | None = None,
                 log_path: str | Path | None = None):
        self._log = open(log_path, "ab") if log_path else None
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._log or subprocess.DEVNULL, env=env, cwd=cwd,
                                     start_new_session=True)
        self.events: Queue = Queue()  # ("notification", method, params) | ("request", id, method, params)
        self.last_activity = time.monotonic()
        self._pending: dict[int, Pending] = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._closed = False
        threading.Thread(target=self._read, name="acp-reader", daemon=True).start()

    @property
    def pid(self) -> int:
        return self.proc.pid

    # -- wire --------------------------------------------------------------------
    def _trace(self, direction: str, line: str) -> None:
        """Protocol traffic in the run's log, next to the agent's stderr ("> " sent, "< " received)."""
        if not self._log:
            return
        line = line.rstrip("\n")
        if len(line) > 2000:
            line = line[:2000] + f"... ({len(line)} chars)"
        with self._log_lock:
            try:
                self._log.write(f"{direction} {line}\n".encode("utf-8", errors="replace"))
                self._log.flush()
            except (OSError, ValueError):
                pass

    def _write(self, msg: dict) -> None:
        data = (json.dumps(msg) + "\n").encode("utf-8")
        self._trace(">", data.decode("utf-8"))
        with self._write_lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                raise AcpError(-32000, f"connection closed: {e}")

    def _read(self) -> None:
        for raw in self.proc.stdout:
            self._trace("<", raw.decode("utf-8", errors="replace"))
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue  # not a protocol line (some agents print banners)
            if not isinstance(msg, dict):
                continue
            self.last_activity = time.monotonic()
            if "method" in msg and "id" in msg:
                self.events.put(("request", msg["id"], msg["method"], msg.get("params") or {}))
            elif "method" in msg:
                self.events.put(("notification", msg["method"], msg.get("params") or {}))
            elif "id" in msg:
                with self._lock:
                    pending = self._pending.pop(msg["id"], None)
                if pending:
                    err = msg.get("error")
                    pending._resolve(msg.get("result"),
                                     AcpError(err.get("code", -32000), err.get("message", "error"), err.get("data"))
                                     if err else None)
        self._closed = True
        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
        for p in pending:
            p._resolve(error=AcpError(-32000, "connection closed: the agent exited"))

    # -- API ---------------------------------------------------------------------
    def request_async(self, method: str, params: dict) -> Pending:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            pending = Pending(method)
            if self._closed:
                pending._resolve(error=AcpError(-32000, "connection closed"))
                return pending
            self._pending[rid] = pending
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        except AcpError as e:
            with self._lock:
                self._pending.pop(rid, None)
            pending._resolve(error=e)
        return pending

    def request(self, method: str, params: dict, timeout: float = 60):
        pending = self.request_async(method, params)
        if not pending.wait(timeout):
            raise AcpError(-32000, f"{method}: timed out after {timeout:g}s")
        return pending.result()

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def respond(self, req_id, result=None, error: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "id": req_id}
        msg.update({"error": error} if error is not None else {"result": result if result is not None else {}})
        self._write(msg)

    def alive(self) -> bool:
        return self.proc.poll() is None and not self._closed

    def close(self, grace: float = 2.0) -> None:
        """Close stdin, give the agent a moment, then stop its whole process group."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    self.proc.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
        if self._log:
            self._log.close()
            self._log = None
