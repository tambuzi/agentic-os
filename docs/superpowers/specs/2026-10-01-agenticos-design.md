# agenticOS — design

Date: 2026-10-01
Status: draft for review

> **Amendment 2026-10-01 (local business data):** business memory, knowledge, the inbox and
> the project registry are local-only, in `$AOS_HOME/data` (configurable `data_dir`, which must be
> outside the repo). The git repo carries only code, skills, `SOUL.md`, `AGENTS.md` and `aos.yaml`.
> Wherever this spec puts `memory/`, `knowledge/`, `inbox/` or `projects.yaml` in the repo, read
> the data dir instead. Team sharing of business data through git is dropped.

## 1. Intent

**What we want.** A team-shared "agentic OS" that gives Claude Code and Kiro a
persistent, self-improving brain across all our projects: shared business
context, per-project context, reusable skills, a learning loop, and a task
board. Code knowledge comes from **graphskill**. Business context today lives in
people's heads, in code, and in Notion/Confluence.

**Model.** A smaller version of [hermes-agent](https://github.com/NousResearch/hermes-agent).
Hermes is a full agent runtime (LLM loop, providers, TUI, messaging gateway,
sandboxes) wrapped around a learning loop (SOUL, capped memory, self-written
skills, background review, session search, skill curator, kanban). agenticOS
keeps **only the learning loop**. Claude Code and Kiro are the runtime.

**Success criteria.**
- `aos link <project>` makes a repo agenticOS-aware in both Claude Code and Kiro
  in one command, idempotently, without clobbering files it does not own.
- Every session in a linked project starts with SOUL + org rules + org memory +
  project memory + user memory injected, under a fixed token budget.
- Agents in either tool can read/write memory, search shared knowledge, read
  another project's context, and create/patch skills through one MCP server.
- Learnings written in one project are visible in every other linked project on
  the next session start, after the team's approval gate where configured.
- graphskill is set up and available in every linked project.

**Out of scope (whole project).** Own LLM loop, model providers, TUI, messaging
gateway, sandboxes, user modeling (honcho), trajectories/RL, plugin system,
dashboard. Cron is deferred.

## 2. Decomposition

| # | Sub-project | Hermes analogue | Delivers |
|---|---|---|---|
| **1** | **Core: repo, link, context, MCP memory/skills/knowledge** | SOUL, MEMORY/USER, AGENTS.md, skills, `skill_manage`, `memory` | Working shared brain in both tools |
| 2 | Learning loop | background review, nudges, approval gate | Stop-hook review → inbox proposals |
| 3 | Recall + hygiene | `session_search`, curator | FTS over past sessions; skill stale/archive |
| 4 | Tasks + ingest | kanban, connectors | Cross-project task board; Notion/Confluence → knowledge |

Each sub-project gets its own plan. **This spec details sub-project 1** and
fixes the interfaces 2–4 plug into (§9).

## 3. Architecture

```
agenticOS (git repo, shared by team)          ~/.agenticos (per user, never committed)
├── SOUL.md                                   ├── config.yaml   repo path, linked projects
├── AGENTS.md          org-wide rules         ├── user.md       user memory
├── memory/                                   └── aos.db        sqlite: knowledge FTS (+ sessions, tasks later)
│   ├── org.md         shared business memory
│   └── projects/<slug>.md
├── knowledge/         long-form docs (ADRs, domain, ingested pages)
├── skills/<name>/SKILL.md                    linked project (any repo)
├── inbox/             proposals awaiting approval   ├── .mcp.json                 + aos, graphskill
├── projects.yaml      registry (slug, desc, repo)   ├── .claude/settings.local.json  + hooks
└── aos/               python package               ├── .claude/skills/<name>/    copies
                                                     ├── .kiro/settings/mcp.json   + aos, graphskill
                                                     ├── .kiro/hooks/aos.json
                                                     ├── .kiro/steering/aos.md
                                                     ├── .kiro/skills/<name>/      copies
                                                     └── .aos/manifest.json        owned paths
```

One Python package (`aos`, Python ≥3.11, deps: `mcp`, `pyyaml`) provides:
- `aos` CLI: for humans and hooks
- `aos serve`: a stdio MCP server, the agent-facing surface

Same split as hermes (agents use tools, humans use the CLI), both over one core
library.

**Content vs. state.** Shared, reviewable content (SOUL, rules, memory,
knowledge, skills) is markdown in the agenticOS git repo, so git is the sync and
review mechanism. Per-user and derived state (config, user memory, search index)
lives in `~/.agenticos/` (override with `$AOS_HOME`).

## 4. Components (sub-project 1)

Each module has one job and is testable without MCP or a real agent.

| Module | Responsibility |
|---|---|
| `aos/config.py` | Load/save `~/.agenticos/config.yaml`; resolve the agenticOS repo root; map project path ↔ slug |
| `aos/store.py` | Atomic, file-locked read/write of markdown files (temp file + rename, `fcntl` lock) |
| `aos/memory.py` | Entry-based capped memory: `read/add/replace/remove` per scope |
| `aos/skills.py` | List/view/create/patch/delete skills in `skills/`; agentskills.io frontmatter validation |
| `aos/knowledge.py` | SQLite FTS5 index over `knowledge/`, `memory/`, `skills/`; lazy rebuild by mtime |
| `aos/inbox.py` | Stage, list, apply, reject proposals |
| `aos/context.py` | Build the session-start context block under a budget |
| `aos/adapters/claude.py` | Render/merge Claude Code files |
| `aos/adapters/kiro.py` | Render/merge Kiro files |
| `aos/link.py` | link / unlink / sync orchestration, manifest, graphskill setup |
| `aos/mcp_server.py` | Thin FastMCP wrapper over the modules above |
| `aos/cli.py` | argparse CLI |

### 4.1 Memory

Hermes-style: small, curated, always in context. Not a dumping ground.

| Scope | File | Cap (chars) | Default write mode |
|---|---|---|---|
| `org` | `memory/org.md` | 4000 | `inbox` (team approval) |
| `project` | `memory/projects/<slug>.md` | 2200 | `direct` |
| `user` | `~/.agenticos/user.md` | 1375 | `direct` |

- Format: a file is a list of entries separated by a line containing only `§`.
  Each entry is free markdown. Multiline entries are allowed.
- `add(scope, text)`: append an entry. If the result would exceed the cap, return
  an error stating current size, cap, and "consolidate or remove entries, then
  retry". No silent truncation (matches hermes).
- `replace(scope, old, new)`: `old` is a substring that must match exactly one
  entry. Zero or multiple matches → error listing the candidates. The cap still
  applies.
- `remove(scope, old)`: same matching rule.
- Write mode per scope is set in `agenticOS/aos.yaml` (`memory.<scope>.mode:
  direct|inbox`). In `inbox` mode the operation is stored as a proposal and the
  tool replies "staged for review as <id>".
- Direct writes to `org`/`project` modify the agenticOS working tree. They reach
  the team when someone commits/pushes. `aos status` lists uncommitted
  memory/skill changes.

### 4.2 Skills

- Canonical location: `agenticOS/skills/<name>/SKILL.md` with optional
  `references/`, `scripts/`, `assets/` (agentskills.io layout, which both Claude
  Code and Kiro load natively).
- Required frontmatter: `name` (kebab-case, equals dir name) and `description`.
  agenticOS adds `metadata.aos: {created_by: human|agent, created: <date>}` for
  the curator in sub-project 3.
- `skill_manage` actions: `create`, `patch` (unique find/replace inside
  SKILL.md), `write_file` (a support file under the skill dir), `delete`.
  Default mode is `direct`, configurable to `inbox`. After a direct write, the
  calling project is re-synced so the skill is usable in the same session.
- Skills tagged `metadata.aos.projects: [slug, …]` sync only to those projects.
  Untagged skills sync everywhere.

### 4.3 Knowledge search

- FTS5 table `docs(path, title, body)` in `aos.db`, covering `knowledge/**/*.md`,
  `memory/**/*.md`, and `skills/*/SKILL.md`.
- Rebuilt on demand when any file mtime is newer than the stored one. This is
  cheap at our scale.
- Results: path, title, snippet, BM25 rank. `knowledge_read(path)` returns the
  full file (path must resolve inside the repo).

### 4.4 Context injection (`aos context`)

Run by the SessionStart hook in both tools. Stdout becomes agent context.

Order (each section has a provenance header):
1. `SOUL.md`
2. `AGENTS.md` (org rules)
3. Memory protocol (fixed text: when to save memory vs. skill, "lessons not
   logs", search before asking, how to use graphskill first for code)
4. `memory/org.md`
5. `memory/projects/<slug>.md`
6. `~/.agenticos/user.md`
7. Linked-project index: slug + one-line description from `projects.yaml`, so
   the agent knows what `project_context` can fetch

Budget: 16k chars by default (`context.max_chars`). Sections 1–6 are capped by
their files. Section 7 is trimmed first if over budget.

Side effect: `aos context` first runs a fast `sync` (skills/steering refresh;
it skips unchanged files by hash), so every session sees the latest shared
content after a `git pull`.

Failure mode: any exception prints one line `[aos] context unavailable: <reason>`
and exits 0. A broken agenticOS must never block a session.

### 4.5 MCP tools (`aos serve --project <path>`)

| Tool | Purpose |
|---|---|
| `memory_read(scope)` | Current entries + usage/cap |
| `memory_add(scope, text)` | §4.1 |
| `memory_replace(scope, old, new)` | §4.1 |
| `memory_remove(scope, old)` | §4.1 |
| `knowledge_search(query, limit=10)` | §4.3 |
| `knowledge_read(path)` | §4.3 |
| `skill_list()` | name + description of all skills |
| `skill_view(name)` | SKILL.md + support file list |
| `skill_manage(action, name, …)` | §4.2 |
| `project_list()` | registered projects |
| `project_context(slug)` | that project's memory + description (cross-project sharing) |

Tool descriptions carry the "when to use" guidance, since that is what the model
reads.

### 4.6 Adapters and `aos link`

`aos link <path> [--name slug] [--targets claude,kiro] [--no-graphskill]`

1. Resolve slug (default: dir name). Add to `projects.yaml` if absent and to
   `config.yaml` (slug → local path).
2. Create `memory/projects/<slug>.md` if absent.
3. **Claude Code**
   - `.mcp.json`: merge `mcpServers.aos = {command: "aos", args: ["serve",
     "--project", "."]}`. `aos` is expected on PATH (installed with `uv tool
     install` / `pipx`), so the file stays portable and committable.
   - `.claude/settings.local.json`: merge a `SessionStart` hook → `aos context
     --project "$CLAUDE_PROJECT_DIR"`. This file is per-user, so each developer
     runs `aos link` once.
   - `.claude/skills/<name>/`: copy synced skills.
4. **Kiro**
   - `.kiro/settings/mcp.json`: merge the same `aos` server.
   - `.kiro/hooks/aos.json`: `{"version":"v1","hooks":[{"name":"aos context",
     "trigger":"SessionStart","action":{"type":"command","command":"aos context
     --project ."}}]}`.
   - `.kiro/steering/aos.md` (`inclusion: always`): short pointer: "agenticOS
     is active; use the aos MCP tools; memory protocol: …". This is a fallback
     for Kiro surfaces where hook context is unavailable.
   - `.kiro/skills/<name>/`: copy synced skills.
5. **graphskill**: if `graphskill` is on PATH and not disabled, run `graphskill
   setup <path>` (writes its `.mcp.json` entry and index), then mirror its
   server entry into `.kiro/settings/mcp.json`. If missing, warn and continue.
6. Write `.aos/manifest.json` listing every path and JSON key aos owns, with
   content hashes. Append `.aos/` and synced skill dirs to `.gitignore`.

**Ownership rules.** JSON files are merged key-by-key; only owned keys are ever
changed or removed. A skill dir or file that exists and is not in the manifest
is never overwritten. It is reported as a conflict and skipped. `unlink`
removes exactly the manifest contents. `sync` = re-render skills and steering
plus prune skills deleted upstream.

Skills are copied, not symlinked. This is safer for Kiro's resolver
(kirodotdev/Kiro#6955) and works on all OSes. Freshness is guaranteed by the
sync inside every `aos context` run.

### 4.7 Inbox

- Proposal = `inbox/<timestamp>-<kind>-<short>.yaml` with `kind`
  (`memory|skill`), `op`, `args`, `source` (project, tool, session id if known),
  and `reason`.
- `aos inbox list | show <id> | apply <id> | reject <id>`. `apply` executes the
  op in `direct` mode. Both commands delete the proposal file.
- Proposals live in the repo, so a teammate can also review them in a PR.
  Sub-project 2 writes into the same inbox.

### 4.8 CLI summary

`aos init [repo]` · `aos link|unlink|sync <path>` · `aos context --project <p>`
· `aos serve --project <p>` · `aos inbox …` · `aos status` · `aos doctor`
(checks PATH, config, repo, graphskill, per-project manifest drift).

## 5. Seed content

- `SOUL.md`: direct, concise team agent persona (adapted from hermes' SOUL).
- `AGENTS.md`: org conventions skeleton with headed sections for the team to
  fill.
- `memory/org.md`: empty.
- `skills/aos-memory/SKILL.md`: when and how to save memory vs. skills, with
  hermes' "lessons, not logs" rules.
- `skills/aos-onboard-project/SKILL.md`: interview the user plus graphskill
  `repo_map` to draft `memory/projects/<slug>.md` for a newly linked repo.
  Captures the "in people's heads" context.
- `aos.yaml`: modes, caps, budget defaults.

## 6. Data flow

```
session start ─► hook: aos context ─► sync skills/steering ─► print SOUL+rules+memories ─► agent context
agent works   ─► MCP aos tools ─► memory/skills/knowledge modules ─► agenticOS working tree (or inbox/)
              ─► MCP graphskill ─► code graph
human         ─► git commit/push agenticOS ─► teammates git pull ─► next session start picks it up
              ─► aos inbox apply ─► org memory updated
```

## 7. Error handling

- Hooks (`context`) always exit 0. Errors become a single bracketed line.
- MCP tools return structured errors (`{"error": "...", "hint": "..."}`) for cap
  overflow, ambiguous matches, unknown scope/skill, and path escape. They never
  raise into the transport.
- File writes are atomic and locked, so concurrent sessions across projects
  cannot corrupt a file. Lost-update risk on memory is accepted: each op re-reads
  under the lock and applies the delta.
- Path safety: every user/agent-supplied path is resolved and must stay inside
  its root (repo, skill dir).
- `link` is idempotent. A second run on an unchanged repo makes zero writes.

## 8. Testing

- pytest with `tmp_path` fixtures for an agenticOS repo, `AOS_HOME`, and a
  project.
- Unit: memory caps/matching/entry parsing, skill frontmatter validation and
  patching, FTS indexing/ranking, inbox round-trip, context ordering/budget.
- Adapter golden tests: link a fixture project, compare generated files to
  expected; second link = no changes; pre-existing user hooks/MCP servers/skills
  survive; unlink restores exactly.
- MCP: call tool functions directly. One smoke test starts `aos serve` over stdio
  and lists tools.
- graphskill: mocked subprocess. Plus an opt-in integration test when it is
  installed.

## 9. Interfaces for later sub-projects

- **2 Learning loop:** Claude `Stop`/`SessionEnd` and Kiro `AgentStop` hook →
  `aos review --enqueue` → detached headless run (`claude -p` or `kiro-cli`)
  with a review prompt over the transcript → writes proposals to `inbox/` using
  §4.7. A nudge counter lives in `aos.db`.
- **3 Recall + hygiene:** `sessions` FTS table in `aos.db` fed from
  `~/.claude/projects/**/*.jsonl` (and Kiro transcripts where available);
  `session_search` MCP tool. `aos curate` uses `metadata.aos` and usage counts
  for active→stale→archived (`skills/.archive/`).
- **4 Tasks + ingest:** `tasks` table in `aos.db` with `task_*` MCP tools and
  `aos task` CLI. Skills `ingest-notion` / `ingest-confluence` drive those
  vendors' MCP servers to distill pages into `knowledge/` and memory proposals.

## 10. Open assumptions (correct me)

- Python + `uv tool install` is acceptable for the team (same as graphskill).
- macOS/Linux only for v1. Windows is untested.
- `org` memory defaults to inbox approval, while `project`, `user` and skills
  default to direct writes.
