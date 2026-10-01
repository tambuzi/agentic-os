"""Claude Code: .mcp.json server, SessionStart hook in settings.local.json, skills."""

from ..writer import LinkContext, Writer, copy_skills

JSON_FILES = [".mcp.json", ".claude/settings.local.json"]


def render(w: Writer, ctx: LinkContext) -> None:
    w.json_key(".mcp.json", ["mcpServers", "aos"], ctx.mcp_server)
    w.hook(".claude/settings.local.json", "SessionStart", ctx.context_command("$CLAUDE_PROJECT_DIR"))
    w.ignore.append(".claude/settings.local.json")
    copy_skills(w, ctx, ".claude/skills")
