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
        self.index = KnowledgeIndex(aos.repo, aos.home / "aos.db")
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


def build_server(tools: Tools):
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

    return mcp


def run_server(project: str | Path) -> None:
    from .link import sync

    cfg = load_user_config()
    repo = repo_root(cfg)
    project = Path(project).expanduser().resolve()
    slug = slug_for_path(project, cfg)

    def resync() -> None:
        if slug:
            sync(project)

    tools = Tools(AOS(repo, slug=slug), on_skills_changed=resync)
    cwd = os.getcwd()
    os.chdir(Path.home())  # FastMCP reads .env from cwd; keep project env files out of it
    try:
        mcp = build_server(tools)
    finally:
        os.chdir(cwd)
    mcp.run()
