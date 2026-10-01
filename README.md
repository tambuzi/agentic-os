# agenticOS

A shared, self-improving brain for **Claude Code** and **Kiro** across all our projects, modelled on the learning loop of [hermes-agent](https://github.com/NousResearch/hermes-agent). Code knowledge comes from **graphskill**.

- **Memory**: small, capped, always-in-context facts: `org` (all projects, team-reviewed), `project`, `user`.
- **Skills**: shared procedures (`skills/<name>/SKILL.md`) that agents can create and patch.
- **Knowledge**: long-form docs in `knowledge/`, searchable by agents.
- **Context injection**: every session starts with SOUL + org rules + memories.

## Install (once per developer)

```bash
git clone <this repo> ~/agenticOS && cd ~/agenticOS
python3.13 -m venv ~/.agenticos/venv && ~/.agenticos/venv/bin/pip install -e .
ln -sf ~/.agenticos/venv/bin/aos ~/.local/bin/aos
aos init . --graphskill "graphskill"             # or '/path/to/venv/bin/python -m graphskill'
aos doctor
```

> **macOS + iCloud Desktop:** keep the runtime venv outside `~/Desktop`/`~/Documents`. iCloud marks
> files there `hidden`, and Python 3.13 silently skips hidden `.pth` files, so editable installs fail
> with `No module named 'aos'`. For running tests, a repo-local `.venv` is fine (`chflags nohidden
> .venv/lib/python3.13/site-packages/*.pth` if needed).

## Use

```bash
aos link ~/code/shop-api --name shop     # Claude Code + Kiro + graphskill
aos link ~/code/crm --targets kiro       # one tool only
aos sync ~/code/shop-api                 # also happens automatically at session start
aos inbox list | show <id> | apply <id> | reject <id>
aos status                               # uncommitted memory/skill changes to push
aos unlink ~/code/shop-api               # removes only what aos added
```

Then open the project in Claude Code or Kiro. Agents get the `aos` MCP tools: `memory_*`, `knowledge_search/read`, `skill_list/view/manage`, `project_list/context`.

**Sharing:** memory, skills and knowledge are files in this repo. Commit and push them; teammates `git pull` and the next session picks them up. Org-memory changes made by agents wait in `inbox/` for review.

## What `aos link` writes

| Claude Code | Kiro |
|---|---|
| `.mcp.json` → `aos` server | `.kiro/settings/mcp.json` → `aos` (+ graphskill mirror) |
| `.claude/settings.local.json` → SessionStart hook | `.kiro/hooks/aos.json` → SessionStart hook |
| `.claude/skills/<name>/` | `.kiro/skills/<name>/`, `.kiro/steering/aos.md` |

Ownership is tracked in `<project>/.aos/manifest.json`; aos never edits files or keys it did not create. Commands use the absolute path of your `aos` executable, so each developer runs `aos link` on their own machine.

## Layout

```
SOUL.md  AGENTS.md  aos.yaml  projects.yaml
memory/org.md  memory/projects/<slug>.md
knowledge/  skills/  inbox/
aos/  (python package)   tests/
```

## Roadmap

1. ✅ Core (this release)
2. Learning loop: Stop-hook background review → inbox proposals
3. Session search + skill curator
4. Task board + Notion/Confluence ingest

Design: `docs/superpowers/specs/2026-10-01-agenticos-design.md`
