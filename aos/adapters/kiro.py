"""Kiro: workspace MCP config, SessionStart hook, always-on steering, skills."""

import json

from ..context import PROTOCOL
from ..store import read_text
from ..writer import LinkContext, Writer, copy_skills

MCP = ".kiro/settings/mcp.json"
JSON_FILES = [MCP, ".mcp.json"]  # .mcp.json is read to mirror graphskill's server


def _steering(slug: str) -> str:
    return (
        "---\ninclusion: always\n---\n\n# agenticOS\n\n"
        f"This workspace is linked to agenticOS as project `{slug}`. Identity, org rules and "
        "memories are injected at session start by the aos hook; if they are missing, call "
        "memory_read for the org, project and user scopes.\n\n" + PROTOCOL + "\n"
    )


def _graphskill_server(ctx: LinkContext):
    try:
        data = json.loads(read_text(ctx.project / ".mcp.json") or "{}")
    except json.JSONDecodeError:
        return None
    return (data.get("mcpServers") or {}).get("graphskill")


def render(w: Writer, ctx: LinkContext) -> None:
    w.json_key(MCP, ["mcpServers", "aos"], ctx.mcp_server)
    gs = _graphskill_server(ctx)
    if gs:
        w.json_key(MCP, ["mcpServers", "graphskill"], gs)
    hooks = {"version": "v1", "hooks": [{
        "name": "agenticOS context",
        "trigger": "SessionStart",
        "action": {"type": "command", "command": ctx.context_command(str(ctx.project))},
        "timeout": 30,
    }]}
    w.file(".kiro/hooks/aos.json", json.dumps(hooks, indent=2) + "\n")
    w.file(".kiro/steering/aos.md", _steering(ctx.slug))
    w.ignore += [".kiro/hooks/aos.json", ".kiro/steering/aos.md"]
    copy_skills(w, ctx, ".kiro/skills")
