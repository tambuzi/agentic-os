"""The agent-facing surface: a stdio MCP server named `aos`.

`Tools` holds the behaviour (testable without MCP); `build_server` only
registers thin wrappers. Tool docstrings are what the model reads, so they
say when to use each tool.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import skills
from .config import load_user_config, repo_root, slug_for_path
from .core import AOS
from .errors import AosError
from .knowledge import KnowledgeIndex
from .board import Board
from .workers.common import profile_settings, resolve_profile


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AosError as e:
            return e.to_dict()
        except Exception as e:  # never raise into the MCP transport
            return {"error": f"internal error: {type(e).__name__}: {e}"}
    return wrapper


class Tools:
    def __init__(self, aos: AOS, on_skills_changed=None):
        self.aos = aos
        self.index = KnowledgeIndex(aos.repo, aos.home / "aos.db", aos.data)
        self.on_skills_changed = on_skills_changed

    def _source(self) -> str:
        return f"project:{self.aos.slug}" if self.aos.slug else "unlinked"

    @_safe
    def memory_read(self, scope: str) -> dict:
        return self.aos.memory_read(scope)

    @_safe
    def memory_add(self, scope: str, text: str, reason: str = "") -> dict:
        return self.aos.memory_write(scope, "add", {"text": text}, self._source(), reason)

    @_safe
    def memory_replace(self, scope: str, old: str, new: str, reason: str = "") -> dict:
        return self.aos.memory_write(scope, "replace", {"old": old, "new": new}, self._source(), reason)

    @_safe
    def memory_remove(self, scope: str, old: str, reason: str = "") -> dict:
        return self.aos.memory_write(scope, "remove", {"old": old}, self._source(), reason)

    @_safe
    def knowledge_search(self, query: str, limit: int = 10) -> dict:
        return {"results": self.index.search(query, limit)}

    @_safe
    def knowledge_read(self, path: str) -> dict:
        return self.index.read(path)

    @_safe
    def skill_list(self) -> dict:
        return {"skills": skills.list_skills(self.aos.repo)}

    @_safe
    def skill_view(self, name: str) -> dict:
        return skills.view(self.aos.repo, name)

    @_safe
    def skill_manage(self, action: str, name: str, content: str | None = None, old: str | None = None,
                     new: str | None = None, path: str | None = None, reason: str = "") -> dict:
        args = {k: v for k, v in {"content": content, "old": old, "new": new, "path": path}.items()
                if v is not None}
        res = self.aos.skill_write(action, name, args, self._source(), reason)
        if res.get("applied") and self.on_skills_changed:
            try:
                self.on_skills_changed()
            except Exception as e:
                res["sync_warning"] = f"skill saved but project sync failed: {e}"
        return res

    @_safe
    def project_list(self) -> dict:
        return {"projects": self.aos.project_list()}

    @_safe
    def project_context(self, slug: str) -> dict:
        return self.aos.project_context(slug)

    @_safe
    def code_tools(self, project: str) -> dict:
        from . import codegraph
        from .workers.common import linked_project_path
        return {"project": project, "tools": codegraph.list_tools(linked_project_path(project))}

    @_safe
    def code_query(self, project: str, tool: str, arguments: dict | None = None) -> dict:
        from . import codegraph
        from .workers.common import linked_project_path
        return {"project": project, **codegraph.query(linked_project_path(project), tool, arguments)}


WORKER_TOOLS = {"task_show", "board_read", "task_comment", "task_block", "task_propose_contract",
                "task_create", "task_complete"}
PLANNER_TOOLS = {"feature_create", "feature_show", "task_create", "board_read", "board_start", "board_status",
                 "task_unblock", "task_retry", "task_cancel", "proposal_decide", "feature_workflow",
                 "board_wait"}


class BoardTools:
    """Board access over MCP. With task_id: a worker's tools for its own task.
    Without: planner tools for creating features and tasks."""

    def __init__(self, aos: AOS, task_id: int | None = None):
        self.aos = aos
        self.board = Board(aos.data, aos.settings["board"]["max_tasks_per_feature"])
        self.task_id = int(task_id) if task_id is not None else None
        self.wait_poll_s = 3.0

    @property
    def author(self) -> str:
        return f"task:{self.task_id}" if self.task_id else "planner"

    def _own(self) -> dict:
        if not self.task_id:
            raise AosError("this tool is only available to board workers")
        return self.board.task(self.task_id)

    def _new_task(self, feature: str, project: str, title: str, spec: str, depends_on) -> dict:
        if project not in self.aos.projects():
            raise AosError(f"unknown project {project!r}", "call project_list; link it with aos link")
        profile = resolve_profile(self.aos, project)
        attempts = profile_settings(self.aos, profile, project)["max_attempts"]
        tid = self.board.add_task(feature, project, title, spec, depends_on or [], worker=profile,
                                  max_attempts=attempts, created_by=self.author)
        return {"task": tid, "worker": profile}

    # -- worker mode ---------------------------------------------------------
    @_safe
    def task_show(self) -> dict:
        t = self._own()
        self.board.mark_seen(t["id"])
        return {"task": t, "feature": self.board.feature(t["feature"]), "brief": self.board.brief(t["feature"]),
                "contract": self.board.contract(t["feature"]),
                "depends_on": self.board.dependency_results(t["id"])}

    @_safe
    def task_comment(self, text: str) -> dict:
        self.board.comment(self._own()["id"], text, self.author)
        return {"ok": True}

    @_safe
    def task_block(self, reason: str) -> dict:
        self.board.block(self._own()["id"], reason, self.author)
        return {"ok": True, "message": "blocked; stop working on this task now"}

    @_safe
    def task_propose_contract(self, change: str, reason: str) -> dict:
        pid = self.board.propose(self._own()["id"], change, reason, self.author)
        return {"proposal": pid, "message": "a human will decide; continue with parts the change doesn't affect"}

    @_safe
    def task_complete(self, summary: str) -> dict:
        self.board.complete(self._own()["id"], summary, self.author)
        return {"ok": True}

    # -- both modes ----------------------------------------------------------
    @_safe
    def board_read(self, feature: str | None = None, since_event: int = 0) -> dict:
        if self.task_id:
            t = self._own()
            feature = t["feature"]
            self.board.mark_seen(t["id"])
        if not feature:
            raise AosError("feature is required")
        return {"events": self.board.events(feature, since_event),
                "contract_version": self.board.feature(feature)["contract_version"]}

    @_safe
    def task_create(self, project: str, title: str, spec: str = "", depends_on: list[int] | None = None,
                    feature: str | None = None) -> dict:
        if self.task_id:
            feature = self._own()["feature"]
        if not feature:
            raise AosError("feature is required")
        return self._new_task(feature, project, title, spec, depends_on)

    # -- planner mode --------------------------------------------------------
    @_safe
    def feature_create(self, slug: str, title: str, brief: str = "", contract: str = "") -> dict:
        return self.board.create_feature(slug, title, brief, contract, author=self.author)

    @_safe
    def board_start(self, feature: str, parallel: int | None = None) -> dict:
        """Start the dispatcher in the background for one feature. It launches a separate
        headless worker per task and exits by itself when nothing more can run."""
        from .dispatcher import dispatcher_status
        self.board.feature(feature)
        if not self.board.tasks(feature=feature):
            raise AosError(f"feature {feature!r} has no tasks", "create tasks with task_create first")
        st = dispatcher_status(self.board.data)
        if st["running"]:
            if st.get("feature") in (None, feature):
                return {"started": False, "dispatcher": st,
                        "message": "dispatcher already running for this feature; use board_status to follow it"}
            raise AosError(f"a dispatcher for feature {st.get('feature')!r} is running (pid {st.get('pid')})",
                           "wait until it finishes (board_status), then call board_start again")
        log = self.board.data / "logs" / f"dispatcher-{feature}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, "-m", "aos", "board", "run", "--feature", feature, "--until-done"]
        if parallel:
            argv += ["--parallel", str(int(parallel))]
        with open(log, "ab") as fh:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                    cwd=str(self.aos.home), start_new_session=True)
        time.sleep(0.5)
        if proc.poll() is not None and proc.returncode != 0:
            tail = "\n".join(log.read_text(errors="replace").splitlines()[-10:])
            raise AosError(f"dispatcher exited with code {proc.returncode}", tail)
        return {"started": True, "pid": proc.pid, "log": str(log),
                "message": "workers run in the background, one per task in its own worktree; "
                           "follow them with board_status. Do not do the tasks yourself."}

    @_safe
    def board_status(self, feature: str) -> dict:
        from .dispatcher import dispatcher_status
        f = self.board.feature(feature)
        tasks = self.board.tasks(feature=feature)
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        blockers = {}
        for e in self.board.events(feature):
            if e["kind"] == "blocker":
                blockers[e["task"]] = e["body"]
        return {
            "feature": {"slug": f["slug"], "title": f["title"], "status": f["status"],
                        "contract_version": f["contract_version"]},
            "dispatcher": dispatcher_status(self.board.data),
            "counts": counts,
            "tasks": [{"id": t["id"], "project": t["project"], "title": t["title"], "status": t["status"],
                       "worker": t["worker"], "attempts": t["attempts"],
                       "result": (t["result"] or "").splitlines()[0] if t["result"] else None} for t in tasks],
            "blocked": [{"task": t["id"], "project": t["project"], "reason": blockers.get(t["id"], "")}
                        for t in tasks if t["status"] == "blocked"],
            "stuck": {str(k): v for k, v in self.board.stuck().items()
                      if any(t["id"] == k for t in tasks)},
            "pending_proposals": self.board.proposals(feature, "pending"),
        }

    @_safe
    def board_wait(self, feature: str, cursor: dict | str | None = None, timeout_sec: float = 300) -> dict:
        """Block (zero model turns) until the feature needs the human, needs board_start,
        is finished, or the timeout passes. Pass back `cursor` so answered/unanswered
        situations already reported are not reported again."""
        from .attention import TERMINAL, task_attention
        from .dispatcher import dispatcher_status
        self.board.feature(feature)
        # clients may hand the cursor back as an object or as its JSON text
        seen = json.loads(cursor) if isinstance(cursor, str) and cursor else (cursor or {})
        deadline = time.monotonic() + max(0.0, min(float(timeout_sec), 900.0))
        while True:
            tasks = self.board.tasks(feature=feature)
            if not tasks:
                raise AosError(f"feature {feature!r} has no tasks")
            stuck = self.board.stuck()
            attention = {}
            for t in tasks:
                sig, info = task_attention(self.board, t, stuck)
                if sig:
                    attention[str(t["id"])] = (sig, info)
            counts: dict[str, int] = {}
            for t in tasks:
                counts[t["status"]] = counts.get(t["status"], 0) + 1
            new_cursor = {k: v[0] for k, v in attention.items()}
            new = [info for k, (sig, info) in attention.items() if seen.get(k) != sig]
            if new:
                return {"reason": "attention", "attention": new, "counts": counts, "cursor": new_cursor}
            if all(t["status"] in TERMINAL for t in tasks):
                return {"reason": "finished", "counts": counts, "cursor": new_cursor,
                        "tasks": [{"task": t["id"], "project": t["project"], "title": t["title"],
                                   "status": t["status"], "result": t["result"]} for t in tasks]}
            running = dispatcher_status(self.board.data)["running"]
            if not running and self.board.dispatchable(feature):
                return {"reason": "stalled", "counts": counts, "cursor": new_cursor,
                        "message": "ready tasks but no dispatcher: call board_start, then board_wait again"}
            if not running and not any(t["status"] in ("running", "ready") for t in tasks):
                return {"reason": "waiting_on_human", "counts": counts, "cursor": new_cursor,
                        "pending": [info for _, info in attention.values()],
                        "message": "nothing can run until the user answers what is pending"}
            if time.monotonic() >= deadline:
                return {"reason": "timeout", "counts": counts, "cursor": new_cursor,
                        "message": "still working; call board_wait again with this cursor"}
            time.sleep(self.wait_poll_s)

    # -- planner mode: act on the human's answers (used by Kiro workflow steps) ----
    def _status(self, tid: int) -> dict:
        t = self.board.task(tid)
        return {"task": t["id"], "status": t["status"]}

    @_safe
    def task_unblock(self, task: int, note: str = "") -> dict:
        self.board.unblock(task, note, author="human")
        return self._status(task)

    @_safe
    def task_retry(self, task: int, note: str = "") -> dict:
        self.board.retry(task, note=note or None, author="human")
        return self._status(task)

    @_safe
    def task_cancel(self, task: int) -> dict:
        self.board.cancel(task, author="human")
        return self._status(task)

    @_safe
    def proposal_decide(self, proposal: int, approve: bool, reason: str = "") -> dict:
        if approve:
            return self.board.approve(proposal, author="human")
        self.board.reject(proposal, reason, author="human")
        return {"rejected": int(proposal)}

    @_safe
    def feature_workflow(self, feature: str, poll: int = 30) -> dict:
        from .kiroflow import write_recipe
        from .workers.common import linked_project_path
        if not self.aos.slug:
            raise AosError("run this from a session inside a linked project",
                           "or use `aos feature workflow <slug> --out <project>`")
        aos_bin = shutil.which("aos") or "aos"
        path = write_recipe(self.board, feature, linked_project_path(self.aos.slug), aos_bin, poll)
        return {"path": str(path),
                "message": "Open Kiro's Workflows panel (enable Workflows in Workspace Configuration) and run "
                           f"aos-{feature}; it starts the workers and asks you when a task needs you."}

    @_safe
    def feature_show(self, slug: str) -> dict:
        return {"feature": self.board.feature(slug), "brief": self.board.brief(slug),
                "contract": self.board.contract(slug), "tasks": self.board.tasks(feature=slug),
                "pending_proposals": self.board.proposals(slug, "pending")}


def build_server(tools: Tools, board_tools: BoardTools | None = None):
    try:  # mcp 2.x renamed FastMCP to MCPServer; same decorator API
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server

    mcp = Server("aos")

    @mcp.tool()
    def memory_read(scope: str) -> dict:
        """Read memory entries and usage. scope: org (all projects) | project (this repo) | user (this person)."""
        return tools.memory_read(scope)

    @mcp.tool()
    def memory_add(scope: str, text: str, reason: str = "") -> dict:
        """Save one durable fact (business rule, decision, convention, preference). Not progress or logs. org changes are staged for team review."""
        return tools.memory_add(scope, text, reason)

    @mcp.tool()
    def memory_replace(scope: str, old: str, new: str, reason: str = "") -> dict:
        """Replace the single entry containing substring `old` with `new`. Use to correct or consolidate."""
        return tools.memory_replace(scope, old, new, reason)

    @mcp.tool()
    def memory_remove(scope: str, old: str, reason: str = "") -> dict:
        """Remove the single entry containing substring `old`."""
        return tools.memory_remove(scope, old, reason)

    @mcp.tool()
    def knowledge_search(query: str, limit: int = 10) -> dict:
        """Search shared business knowledge, memories and skills across all projects. Use before asking the user for business context."""
        return tools.knowledge_search(query, limit)

    @mcp.tool()
    def knowledge_read(path: str) -> dict:
        """Read a full markdown document returned by knowledge_search."""
        return tools.knowledge_read(path)

    @mcp.tool()
    def skill_list() -> dict:
        """List shared skills (name + description)."""
        return tools.skill_list()

    @mcp.tool()
    def skill_view(name: str) -> dict:
        """Show a skill's SKILL.md and its support files."""
        return tools.skill_view(name)

    @mcp.tool()
    def skill_manage(action: str, name: str, content: str | None = None, old: str | None = None,
                     new: str | None = None, path: str | None = None, reason: str = "") -> dict:
        """Save procedural knowledge after a non-trivial task, a dead end, or a user correction. action: create (content = full SKILL.md with name/description frontmatter) | patch (old -> new, exact unique match) | write_file (path, content) | delete (archives)."""
        return tools.skill_manage(action, name, content, old, new, path, reason)

    @mcp.tool()
    def project_list() -> dict:
        """List all projects linked to agenticOS."""
        return tools.project_list()

    @mcp.tool()
    def project_context(slug: str) -> dict:
        """Read another project's description and memory."""
        return tools.project_context(slug)

    @mcp.tool()
    def code_tools(project: str) -> dict:
        """List the graphskill tools available for a linked project's code graph (any project, not only this one)."""
        return tools.code_tools(project)

    @mcp.tool()
    def code_query(project: str, tool: str, arguments: dict | None = None) -> dict:
        """Query any linked project's code graph via its graphskill server, e.g. tool="repo_map", "search_symbols" {"query": ...}, "search_semantic", "callers", "read_symbol_body". Use this for projects other than the current one."""
        return tools.code_query(project, tool, arguments)

    if board_tools is not None:
        _register_board_tools(mcp, board_tools)
    return mcp


def _register_board_tools(mcp, bt: BoardTools) -> None:
    if bt.task_id:
        @mcp.tool()
        def task_show() -> dict:
            """Your board task: spec, feature brief, current contract and the results of tasks you depend on. Call first."""
            return bt.task_show()

        @mcp.tool()
        def board_read(since_event: int = 0) -> dict:
            """The feature timeline (other workers' progress, contract changes). Call before each step and before finishing."""
            return bt.board_read(since_event=since_event)

        @mcp.tool()
        def task_comment(text: str) -> dict:
            """Post progress or a note for other workers and the human."""
            return bt.task_comment(text)

        @mcp.tool()
        def task_block(reason: str) -> dict:
            """Stop: you cannot continue without a human. Explain exactly what is needed."""
            return bt.task_block(reason)

        @mcp.tool()
        def task_propose_contract(change: str, reason: str) -> dict:
            """Propose a change to the shared contract between projects; keep working on unaffected parts."""
            return bt.task_propose_contract(change, reason)

        @mcp.tool()
        def task_create(project: str, title: str, spec: str = "", depends_on: list[int] | None = None) -> dict:
            """Add a follow-up task to this feature (any linked project)."""
            return bt.task_create(project, title, spec, depends_on)

        @mcp.tool()
        def task_complete(summary: str) -> dict:
            """Finish your task: what changed, commits, how it was tested, what dependent tasks need to know."""
            return bt.task_complete(summary)
    else:
        @mcp.tool()
        def feature_create(slug: str, title: str, brief: str = "", contract: str = "") -> dict:
            """Create a multi-project feature with its brief and the shared contract (APIs, events, schemas)."""
            return bt.feature_create(slug, title, brief, contract)

        @mcp.tool()
        def feature_show(slug: str) -> dict:
            """A feature's brief, contract, tasks and pending contract proposals."""
            return bt.feature_show(slug)

        @mcp.tool()
        def task_create(project: str, title: str, feature: str, spec: str = "",
                        depends_on: list[int] | None = None) -> dict:
            """Add a task for one project to a feature; depends_on lists task ids that must finish first."""
            return bt.task_create(project, title, spec, depends_on, feature=feature)

        @mcp.tool()
        def board_read(feature: str, since_event: int = 0) -> dict:
            """A feature's timeline."""
            return bt.board_read(feature=feature, since_event=since_event)

        @mcp.tool()
        def board_start(feature: str, parallel: int | None = None) -> dict:
            """Start the feature's tasks: launches separate headless workers (one per task, each in its own project worktree) in the background. After planning, call this instead of doing the tasks yourself."""
            return bt.board_start(feature, parallel)

        @mcp.tool()
        def board_wait(feature: str, cursor: dict | str | None = None, timeout_sec: float = 300) -> dict:
            """Wait (no tokens spent) until the feature needs the user (reason "attention"), needs board_start ("stalled"), is "finished", is "waiting_on_human" for an answer, or "timeout" passes. Always pass back the returned cursor."""
            return bt.board_wait(feature, cursor, timeout_sec)

        @mcp.tool()
        def task_unblock(task: int, note: str = "") -> dict:
            """Unblock a task with the human's answer (the note is passed to the worker)."""
            return bt.task_unblock(task, note)

        @mcp.tool()
        def task_retry(task: int, note: str = "") -> dict:
            """Queue a failed/blocked/cancelled task again, with the human's guidance as a note."""
            return bt.task_retry(task, note)

        @mcp.tool()
        def task_cancel(task: int) -> dict:
            """Cancel a task (a running worker is stopped)."""
            return bt.task_cancel(task)

        @mcp.tool()
        def proposal_decide(proposal: int, approve: bool, reason: str = "") -> dict:
            """Approve (new contract version) or reject a worker's contract proposal, on the human's decision."""
            return bt.proposal_decide(proposal, approve, reason)

        @mcp.tool()
        def feature_workflow(feature: str, poll: int = 30) -> dict:
            """Kiro only: write a Kiro workflow (.kiro/workflows/aos-<feature>.workflow.yaml) that runs the feature on the board and asks the user in Kiro when a task needs them."""
            return bt.feature_workflow(feature, poll)

        @mcp.tool()
        def board_status(feature: str) -> dict:
            """Progress of a feature: task statuses and results, blockers, stuck tasks, pending contract proposals, whether workers are running."""
            return bt.board_status(feature)


def run_server(project: str | Path, task: int | None = None) -> None:
    from .link import sync

    cfg = load_user_config()
    repo = repo_root(cfg)
    project = Path(project).expanduser().resolve()
    slug = slug_for_path(project, cfg)

    def resync() -> None:
        if slug:
            sync(project)

    aos = AOS(repo, slug=slug)
    tools = Tools(aos, on_skills_changed=resync)
    board_tools = BoardTools(aos, task_id=task)
    cwd = os.getcwd()
    os.chdir(Path.home())  # FastMCP reads .env from cwd; keep project env files out of it
    try:
        mcp = build_server(tools, board_tools)
    finally:
        os.chdir(cwd)
    mcp.run()
