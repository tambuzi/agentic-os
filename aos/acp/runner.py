"""AcpRun: one live ACP worker or reviewer, as the dispatcher sees it.

It owns the agent process (via AcpConnection) and its session. The dispatcher services it
on its own thread: drains events (cost, permission requests), notices the end of a turn,
and closes it; the reader thread never touches the board.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..store import read_text
from .rpc import AcpConnection
from .session import AcpWorkerSession
from .tools import launch


class AcpRun:
    def __init__(self, tool: str, settings: dict, home: str | Path, log_path: str | Path):
        self.tool = tool
        self.settings = settings
        self.home = home
        self.log_path = log_path
        self.conn: AcpConnection | None = None
        self.session: AcpWorkerSession | None = None
        self.turn_done_at: float | None = None
        self.closed = False
        self.nudged = False
        self.stalled = False
        self.reprompt: str | None = None  # Kiro steer --now: the next prompt once the turn has ended
        self.end_reason: str | None = None  # why the session ended other than by a verdict (refusal, error)
        self.refusal_retried = False
        self._timed_turn = None

    @property
    def proc(self):
        return self.conn.proc

    def start(self, spec, kiro_agent: str | None = None, resume_id: str | None = None) -> None:
        """Raises tools.AdapterMissing before anything is started, AcpError after."""
        argv, env = launch(self.tool, self.settings, self.home)
        self.conn = AcpConnection(argv, env=env, cwd=spec.worktree, log_path=self.log_path)
        self.session = AcpWorkerSession(self.conn, self.tool)
        try:
            self.session.start(spec.worktree, spec.mcp_servers, read_text(spec.context_file),
                               resume_id=resume_id, kiro_agent=kiro_agent, model=getattr(spec, "model", None))
            self.session.prompt(spec.prompt)
        except Exception:
            self.close()
            raise

    def turn_finished(self) -> bool:
        """The current turn has ended (and, for Kiro, its trailing metadata has arrived or
        a few seconds passed, so its credit cost isn't lost)."""
        turn = self.session.turn if self.session else None
        if turn is not self._timed_turn:  # a new prompt: its own wait for the trailing metadata
            self._timed_turn, self.turn_done_at = turn, None
        if turn is None or not turn.done():
            self.turn_done_at = None
            return False
        if self.turn_done_at is None:
            self.turn_done_at = time.monotonic()
        if self.session.facts["turn_end"] and not self.session._turn_end.is_set():
            return time.monotonic() - self.turn_done_at > 5
        return True

    def close(self) -> None:
        if not self.closed and self.conn:
            self.closed = True
            self.conn.close()
