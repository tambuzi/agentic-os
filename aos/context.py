"""The text injected at session start (Claude Code SessionStart / Kiro SessionStart hook)."""

from __future__ import annotations

from .core import AOS
from .store import read_text

PROTOCOL = """\
You are running with agenticOS, the team's shared, persistent brain across projects. Its tools are on the `aos` MCP server.

Memory (loaded below, small on purpose):
- Save durable facts only: business rules, domain terms, decisions and their reasons, conventions, environment quirks, user preferences. Never task progress, logs, or anything derivable from the code.
- Scopes: `org` holds for every project and is staged for team review; `project` is this repo; `user` is this person.
- Use memory_add, memory_replace and memory_remove. When a scope is full, consolidate entries, then retry.

Skills (procedures, loaded on demand):
- After a non-trivial multi-step task, a dead end you worked around, or a user correction, save or patch a skill with skill_manage. Write lessons, not logs: the steps that work, the pitfalls, the expected result.
- Check skill_list first; patch an existing skill instead of creating a near-duplicate.

Context:
- Before asking the user about business context, call knowledge_search. For another project, use project_list and project_context.
- For code questions use the graphskill tools (repo_map, search_symbols, callers, read_symbol_body) before grep or reading whole files."""

TRUNC = "\n…[aos: context truncated]\n"


def build_context(aos: AOS) -> str:
    parts = ["# agenticOS context"]

    def section(title: str, text: str) -> None:
        parts.append(f"## {title}\n\n{text.strip()}")

    soul = read_text(aos.repo / "SOUL.md")
    rules = read_text(aos.repo / "AGENTS.md")
    if soul.strip():
        section("Identity (SOUL.md)", soul)
    if rules.strip():
        section("Org rules (AGENTS.md)", rules)
    section("agenticOS memory protocol", PROTOCOL)

    labels = {"org": "Org memory", "project": f"Project memory: {aos.slug}", "user": "User memory"}
    for scope in ("org", "project", "user"):
        if scope == "project" and not aos.slug:
            continue
        snap = aos.memory(scope).snapshot()
        section(f"{labels[scope]} [{snap['used']}/{snap['cap']} chars]",
                "\n§\n".join(snap["entries"]) or "(empty)")

    out = "\n\n".join(parts) + "\n"
    limit = int(aos.settings["context"]["max_chars"])

    lines = [f"- {p['slug']}: {p['description']}" if p["description"] else f"- {p['slug']}"
             for p in aos.project_list() if p["slug"] != aos.slug]
    if lines:
        head = "\n## Other projects (call project_context for details)\n\n"
        kept, size = [], len(out) + len(head)
        for line in lines:
            if size + len(line) + 1 > limit:
                break
            kept.append(line)
            size += len(line) + 1
        if kept:
            out += head + "\n".join(kept) + "\n"

    if len(out) > limit:
        out = out[: limit - len(TRUNC)] + TRUNC
    return out
