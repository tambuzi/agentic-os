# agenticOS

A shared, self-improving brain for **Claude Code** and **Kiro** across all your projects. It is modelled on the learning loop of [hermes-agent](https://github.com/NousResearch/hermes-agent), and code knowledge comes from **graphskill**.

agenticOS is not another agent. Claude Code and Kiro still do the work. agenticOS gives them five things:

| | What it is | Where it lives |
|---|---|---|
| **Identity & rules** | `SOUL.md` (how the agent behaves) and `AGENTS.md` (org-wide rules) | this repo |
| **Memory** | Small, capped facts that are injected into every session: `org`, `project`, `user` | local: `~/.agenticos/data/memory`, `~/.agenticos/user.md` |
| **Knowledge** | Long-form business docs that agents search on demand | local: `~/.agenticos/data/knowledge` |
| **Skills** | Step-by-step procedures that agents load when relevant, and can write themselves | this repo: `skills/` |
| **Code graph** | Symbols, callers, dependencies (graphskill) | per project, managed by graphskill |

**Business data never leaves your machine.** Memory, knowledge, the inbox and the project list live in `~/.agenticos/data`. That folder is outside this repo and outside iCloud-synced folders. Only code, skills, `SOUL.md`, `AGENTS.md` and `aos.yaml` go to GitHub.

---

## 1. Setup (once per machine)

**Requirements:** macOS or Linux, Python ≥ 3.11, git. Claude Code and/or Kiro.

```bash
# 1. Get the code
git clone https://github.com/tambuzi/agentic-os.git ~/agenticOS
cd ~/agenticOS

# 2. Install the `aos` command in its own venv (outside ~/Desktop and ~/Documents, see note)
python3 -m venv ~/.agenticos/venv
~/.agenticos/venv/bin/pip install -e .
mkdir -p ~/.local/bin && ln -sf ~/.agenticos/venv/bin/aos ~/.local/bin/aos
aos --help                       # if "command not found": add ~/.local/bin to your PATH

# 3. Point aos at this repo (also creates ~/.agenticos/data)
aos init ~/agenticOS --graphskill "graphskill"
#   --graphskill: how to run graphskill, e.g. "/path/to/venv/bin/python -m graphskill"
#   --data-dir:   optional other place for business data (must be outside the repo)

# 4. Check
aos doctor
```

You want `doctor` to show `ok` for `aos on PATH`, `repo`, and `graphskill`. graphskill is optional: without it, linking still works and code-graph setup is skipped with a warning.

> **macOS + iCloud:** do not put the venv under `~/Desktop` or `~/Documents`. iCloud marks files there
> `hidden`, and Python 3.13 silently ignores hidden `.pth` files, so the install breaks with
> `No module named 'aos'`. Check with `ls -lO <venv>/lib/python3.*/site-packages/*.pth`.

## 2. Connect a project

```bash
aos link ~/code/shop-api --name shop          # Claude Code + Kiro + graphskill
aos link ~/code/crm --targets kiro            # only one tool
```

Then **restart Claude Code / Kiro in that project.** `aos link` writes:

| Claude Code | Kiro |
|---|---|
| `.mcp.json`: `aos` server (portable, safe to commit) | `.kiro/settings/mcp.json`: `aos` server (+ graphskill) |
| `.claude/settings.local.json`: SessionStart hook | `.kiro/hooks/aos.json`: SessionStart hook |
| `.claude/skills/<name>/` (copies) | `.kiro/skills/<name>/`, `.kiro/steering/aos.md` |

aos records everything it adds in `<project>/.aos/manifest.json` and **never edits files or settings it didn't create**. It also adds a managed block to the project's `.gitignore`. `aos unlink` removes exactly what it added.

**First session in a new project:** ask the agent to *"onboard this project into agenticOS"*. The `aos-onboard-project` skill reads the code graph, asks you up to five questions about what the code can't tell (purpose, dependencies, invariants, environments, gotchas), and saves the answers as project memory.

## 3. Everyday use

You mostly just work as usual. Every session in a linked project automatically starts with:

1. `SOUL.md` and `AGENTS.md`
2. the memory protocol (how the agent should save what it learns)
3. org memory, this project's memory, your user memory
4. a list of the other linked projects

The agent also gets these tools (MCP server `aos`):

| Tool | Use |
|---|---|
| `memory_read / memory_add / memory_replace / memory_remove` | read and curate memory (`org`, `project`, `user`) |
| `knowledge_search / knowledge_read` | search business docs, memory and skills |
| `skill_list / skill_view / skill_manage` | find, read, create and patch skills |
| `project_list / project_context` | read another project's memory |
| graphskill tools (`repo_map`, `search_symbols`, `callers`, …) | navigate code without reading whole files |

Things you can say to the agent:
- *"Remember that invoices are issued on the 1st because finance closes on the last business day."* → memory
- *"Save how we just fixed the deploy as a skill."* → skill
- *"What do we know about refunds?"* → knowledge search
- *"What does the crm project depend on?"* → another project's context

### Commands

```bash
aos inbox list                    # org-memory proposals waiting for approval
aos inbox show <id>
aos inbox apply <id>              # accept → written to org memory
aos inbox reject <id>
aos sync <project>                # re-copy skills/config (also runs at every session start)
aos status                        # uncommitted skill changes + inbox count
aos doctor                        # health check
aos unlink <project>
```

### Settings: `aos.yaml` (in this repo)

```yaml
memory:
  org:     {cap: 4000, mode: inbox}   # inbox = agent changes need `aos inbox apply`
  project: {cap: 2200, mode: direct}  # direct = written immediately
  user:    {cap: 1375, mode: direct}
skills:
  mode: direct                        # or inbox, to review agent-written skills
context:
  max_chars: 16000                    # size limit of the session-start context
```

---

## 4. How the knowledge grows

agenticOS keeps two kinds of knowledge, and they grow differently.

### Memory: small facts, always loaded

Memory is deliberately small: it is injected into *every* session, so every character costs tokens. Caps: org 4000, project 2200, user 1375 characters.

It grows when:
1. **You tell the agent something worth keeping.** The agent calls `memory_add`.
2. **The agent learns something durable on its own**, such as a business rule, a decision and its reason, a quirk, or a preference. The memory protocol in every session tells it to save these, and tells it to skip task progress or anything the code already says.
3. **Onboarding a project** with the `aos-onboard-project` skill.

Write rules per scope:
- `project` and `user` changes are written immediately.
- `org` changes are **staged in the inbox**. Nothing becomes org-wide truth until a human runs `aos inbox apply`.

Memory also **shrinks on purpose**. When a scope is full, the write fails and the agent must consolidate: merge entries, replace outdated ones, remove stale ones. Over time memory converges to the facts that matter most, instead of piling up.

### Knowledge docs: long-form, searched on demand

`~/.agenticos/data/knowledge/` is for anything too long for memory: domain model, glossary, pricing rules, ADRs, onboarding notes, pages copied from Notion/Confluence.

It grows when **you add markdown files** there:

```bash
$EDITOR ~/.agenticos/data/knowledge/pricing.md      # one topic per file, "# Title" on line 1
```

They are searchable on the agent's next `knowledge_search`. There is no rebuild step: the index notices new and changed files by itself.

### Skills: procedures that improve with use

When the agent finishes a non-trivial task, finds its way past a dead end, or gets corrected by you, the protocol tells it to save or patch a skill (`skill_manage`). Skills are written into this repo's `skills/`. Review them with `git diff` / `aos status`, then commit and push to share them with the team.

### Today vs. next

| Growth path | Status |
|---|---|
| Agent saves memory and skills during a session (when prompted by the protocol or by you) | ✅ works now |
| Org memory gated by human approval (inbox) | ✅ works now |
| Humans add knowledge docs by dropping markdown in `data/knowledge/` | ✅ works now |
| Agent writes long-form knowledge docs itself | ❌ not yet: agents have no `knowledge_write` tool; they save to memory or a skill instead |
| **Automatic learning loop**: when a session ends, a background review reads the transcript and proposes memory/skill updates to the inbox (hermes-style) | 🔜 sub-project 2 |
| Search past sessions; prune unused or duplicate skills | 🔜 sub-project 3 |
| Import from Notion/Confluence into `knowledge/` | 🔜 sub-project 4 |
| Multi-project features with parallel Claude Code / Kiro workers (board) | ✅ works now |

Until sub-project 2 ships, learning only happens while a session is running. The agent saves things when the protocol or you prompt it. Saying *"save what you learned"* at the end of a session is a good habit.

---

## 5. Multi-project features (board)

One feature, several projects, several agents in parallel. Each agent works in its own project, knows the shared feature context, and coordinates through a local board.

```bash
# 1. Plan. Interactively: ask the agent to "plan a feature" (aos-plan-feature skill),
#    or by hand:
aos feature new checkout-v2 --title "Checkout v2" --projects shop-api,web,billing
$EDITOR ~/.agenticos/data/features/checkout-v2/brief.md      # goal, scope
$EDITOR ~/.agenticos/data/features/checkout-v2/contract.md   # APIs/events shared between projects
aos task add checkout-v2 shop-api "Orders endpoint"
aos task add checkout-v2 web "Checkout UI" --after 1
aos task add checkout-v2 billing "Invoice on order" --after 1 --worker kiro

# 2. Run: workers start in parallel (one per project at a time), each in its own git worktree
aos board run --feature checkout-v2

# 3. Watch and steer (another terminal)
aos board                                   # status, blockers, stuck tasks, pending proposals
aos task show 2 --log                       # what a worker did
aos feature approve 4 | reject 4            # contract change proposed by a worker
aos task unblock 3 --note "key is in vault" # answer a blocker
aos task retry 2 --resume --note "rename the button"   # feedback on a finished task
```

**What each worker gets.**
- *General:* SOUL, rules, org memory, the feature brief, the current contract, and the latest timeline.
- *Specific:* its project's memory, its task spec, the results of the tasks it depends on, and graphskill.
- *Tools:* `task_show`, `board_read`, `task_comment`, `task_block`, `task_propose_contract`, `task_create`, `task_complete`.

**Where the work lands.** `~/.agenticos/worktrees/<feature>/<project>` on branch `feature/<feature>`. Your own checkouts are never touched, and nothing is pushed. Review the branches, then merge or open PRs yourself.

**Claude Code or Kiro.** Each task runs on a worker profile:
1. `--worker` on the task;
2. `projects.<slug>.worker.profile` in `~/.agenticos/data/projects.yaml`;
3. `board.default_worker` in `aos.yaml`.

Kiro workers need `kiro-cli` with `KIRO_API_KEY` (or a login). Allowed tools per project:
```yaml
# ~/.agenticos/data/projects.yaml
projects:
  shop-api:
    worker: {profile: claude, allowed_tools: ["shell:npm test", "shell:npm run lint"]}
```
Workers never get "allow everything": only the listed tools, plus file edits inside their worktree.

**Data.** The board is one SQLite file, `~/.agenticos/data/board.db` (no server). Feature files, logs and run contexts sit next to it. All of it is local.

## Layout

```
agenticOS repo (GitHub)               ~/.agenticos (local only, never pushed)
  SOUL.md  AGENTS.md  aos.yaml          config.yaml   which repo, linked projects, graphskill cmd
  skills/<name>/SKILL.md                user.md       your personal memory
  aos/   python package                 aos.db        search index (derived, safe to delete)
  tests/ docs/                          venv/         the installed aos
                                        data/
                                          memory/org.md, memory/projects/<slug>.md
                                          knowledge/*.md
                                          inbox/*.yaml
                                          projects.yaml
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No module named 'aos'` | venv is in an iCloud folder; recreate it at `~/.agenticos/venv` |
| Agent doesn't mention agenticOS context | restart the tool in the project; run `aos context --project <path>` to see what it should get |
| `aos` MCP server not available in Claude Code | approve the project's MCP server when prompted; check `aos` is on PATH |
| `conflict, left untouched: ...` from `aos link` | a file or setting with that name already exists and isn't aos's; rename or remove it, then re-link |
| graphskill warning in `aos doctor` | install graphskill, then `aos init <repo> --graphskill "<cmd>"` and re-run `aos link` |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

Design spec: `docs/superpowers/specs/2026-10-01-agenticos-design.md` · Plan: `docs/superpowers/plans/2026-10-01-agenticos-core.md`
