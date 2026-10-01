"""The agent-facing surface: a stdio MCP server named `aos`.

`Tools` holds the behaviour (testable without MCP); `build_server` only
registers thin wrappers. Tool docstrings are what the model reads, so they
say when to use each tool.
"""

from __future__ import annotations

import functools
import os
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


WORKER_TOOLS = {"task_show", "board_read", "task_comment", "task_block", "task_propose_contract",
                "task_create", "task_complete"}
PLANNER_TOOLS = {"feature_create", "feature_show", "task_create", "board_read"}


class BoardTools:
    """Board access over MCP. With task_id: a worker's tools for its own task.
    Without: planner tools for creating features and tasks."""

    def __init__(self, aos: AOS, task_id: int | None = None):
        self.aos = aos
        self.board = Board(aos.data, aos.settings["board"]["max_tasks_per_feature"])
        self.task_id = int(task_id) if task_id is not None else None

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
