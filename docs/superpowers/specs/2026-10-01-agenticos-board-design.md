# agenticOS Board: multi-project features with parallel agents

Date: 2026-10-01
Status: approved design, pending spec review
Builds on: `2026-10-01-agenticos-design.md` (core). Replaces sub-project 4's "task board" item. Notion/Confluence ingest stays a separate later item.

## 1. Intent

**What we want.** A feature often touches several projects (e.g. `shop-api`, `web`, `billing`). We want several agents working on it at the same time, one per project. Each agent should get:
- the **general feature context**: goal, shared contract between projects, decisions, what the other agents are doing;
- **its own project's context**: project memory, its task, and results of the tasks it depends on.

**Chosen shape (approach C).** A general, durable **task board** with a **dispatcher** that launches headless workers. A feature is a group of tasks across projects with dependencies. This is the hermes-kanban idea, scaled down and built on Claude Code.

**Decisions taken with the user:**
- The orchestrator launches workers. You don't open the sessions yourself.
- Each worker works in an isolated git worktree on `feature/<slug>`, commits with tests, and stops. Nothing is pushed.
- Contract changes are proposed by workers and approved or rejected by a human. Workers keep working on the parts the change doesn't affect.
- All board data is local (`~/.agenticos/data`), like the rest of agenticOS business data. `board.db` is a plain SQLite **file**, not a database server: every `aos` process opens it directly.
- **Workers must run on Claude Code and on Kiro.** Each task runs on one worker profile (`claude` or `kiro`), chosen per task, per project, or by default.

**Success criteria:**
- `aos feature new` plus planning produce a brief, a contract and tasks across ≥2 projects with dependencies.
- `aos board run` runs ready tasks in parallel across projects, one at a time per project worktree, and never touches the user's checkouts.
- Every worker starts with feature context plus project context and can read and post to the shared timeline.
- An approved contract change is visible to all workers of the feature. A worker cannot complete a task against a contract version it has not seen.
- A crashed or timed-out worker is retried up to a limit, then left `failed` for a human.

**Out of scope (v1):**
- Pushing or opening PRs.
- A background daemon, cron, or a web dashboard.
- Merging worktrees.
- Notion/Confluence ingest.

## 2. Concepts

| Concept | Meaning |
|---|---|
| **Feature** | A named piece of work spanning projects. It has `brief.md`, a versioned `contract.md`, tasks and a timeline. |
| **Task** | One unit of work in one linked project. It has a spec, a status, dependencies and attempts. |
| **Dependency** | Task B waits until task A is `done`. A's result summary is passed to B. |
| **Event** | An entry on the feature timeline: comment, status change, blocker, proposal, decision, result. |
| **Proposal** | A requested contract change, decided by a human. |
| **Worker** | A headless agent process that runs one task attempt. It uses a **profile**: `claude` (Claude Code `claude -p`) or `kiro` (`kiro-cli chat --no-interactive`). |
| **Worktree** | `~/.agenticos/worktrees/<feature>/<project>`, branch `feature/<feature>`. One per feature and project, shared by that project's tasks sequentially. |

## 3. Data (local, `$AOS_DATA` = `~/.agenticos/data`)

```
data/
  board.db                      SQLite (WAL), source of truth for state
  features/<slug>/brief.md      human/planner-written
  features/<slug>/contract.md   current contract (header records version)
  features/<slug>/contract.v<N>.md   previous versions (kept)
  logs/<task-id>-<attempt>.log  worker stdout/stderr
```

`board.db` schema:

```sql
features(slug TEXT PK, title TEXT, status TEXT,          -- open | done | cancelled
         contract_version INT DEFAULT 1, created TEXT)
tasks(id INTEGER PK, feature TEXT, project TEXT, title TEXT, spec TEXT,
      status TEXT,                                       -- see §4
      worker TEXT,                                       -- profile: claude | kiro (resolved at creation, see §6)
      attempts INT DEFAULT 0, max_attempts INT,
      contract_seen INT,                                 -- version the current attempt started with / last re-read
      result TEXT, session_id TEXT, pid INT,
      started TEXT, updated TEXT, created_by TEXT)
deps(task INT, depends_on INT, PRIMARY KEY(task, depends_on))
events(id INTEGER PK, feature TEXT, task INT NULL, ts TEXT,
       kind TEXT,      -- comment | status | blocker | proposal | decision | result | contract
       author TEXT,    -- 'human' | 'dispatcher' | 'task:<id>'
       body TEXT)
proposals(id INTEGER PK, feature TEXT, task INT, body TEXT, reason TEXT,
          status TEXT, -- pending | approved | rejected
          decided TEXT)
```

Every state change goes through `aos/board.py` in a transaction (`BEGIN IMMEDIATE`). A status change always writes an `events` row. The CLI, the MCP tools and the dispatcher share this one module.

## 4. Task lifecycle

```
todo ──(all deps done)──► ready ──(dispatcher)──► running ──task_complete──► done
                                                     │
                                                     ├─ task_block ──► blocked ──(aos task unblock)──► ready
                                                     ├─ exit without complete / timeout ──► ready  (attempts < max)
                                                     │                                    └► failed (attempts = max)
any non-done ──(aos task cancel)──► cancelled         failed ──(aos task retry)──► ready (attempts reset)
```

- **Promotion:** `todo → ready` when every dependency is `done`. A dependency that is `cancelled` or `failed` keeps the task `todo` and surfaces it in `aos board` as "waiting on failed/cancelled task".
- **Dependency cycles** are rejected when a dependency is added.
- **A blocked worker** records the blocker, finishes its process, and frees the worktree.

## 5. Dispatcher: `aos board run [--feature F] [--parallel N] [--once]`

A foreground loop with a 2 s tick. Each tick:
1. **Reap** finished worker processes. If the task is not `done`/`blocked`/`cancelled`, count a failed attempt and return it to `ready` or set it `failed`. Kill workers that exceed `timeout_min` and treat them the same way.
2. **Promote** `todo → ready`.
3. **Pick** `ready` tasks in creation order, skipping any whose `(feature, project)` worktree already has a running task, until `parallel` (default 3) workers are running.
4. **Prepare** the worktree:
   - Create it with `git -C <project> worktree add -B feature/<slug> <path>` if missing, then reuse it.
   - Refuse (task → `blocked`, with a reason) if the project path is not a git repo, or if `<path>` exists and is not a worktree of that repo.
5. **Launch** the worker (§6), set `running`, `attempts+1`, `contract_seen = current version`, record `pid` and `session_id`, and log to `logs/`.

Other rules:
- **Ctrl-C** stops launching new workers, waits for running ones (a second Ctrl-C terminates them), and exits.
- **`--once`** runs one tick and waits for the launched workers to finish. It is used by tests and by scripts.
- **Single dispatcher:** a lock file (`data/.dispatcher.lock`, flock) prevents two dispatchers from running.

## 6. Workers

Both tools get the same inputs. Only the launch mechanics differ, so each profile is a small adapter module with one function:
`prepare(task, ctx) -> Launch(argv, cwd, env, session_hint, cleanup)`.

**Profile choice**, first match wins:
1. `aos task add --worker kiro|claude`;
2. `projects.<slug>.worker.profile` in the local `projects.yaml`;
3. `board.default_worker` in `aos.yaml` (default `claude`).

The choice is stored on the task, and `aos task retry --worker X` can switch it.

### 6.1 Shared inputs (built by `aos/workers/common.py`)
- **cwd:** the worktree.
- **context:** written to `data/runs/<task>-<attempt>/context.md`. It contains:
  - `aos context` for that project: SOUL, AGENTS, protocol, memories;
  - the **worker protocol** (below);
  - the feature brief and the current contract, with its version;
  - the task spec and the `result` of each dependency;
  - the last 30 feature events.
- **prompt:** "Do task #N: <title>. Follow the worker protocol." On resume, your note is used instead.
- **MCP servers:**
  - `aos`, run as `<aos> serve --project <project path> --task <id>`;
  - `graphskill`, copied from the project's `.mcp.json` if present.
- **Allowed tools** come from the profile config plus `projects.<slug>.worker.allowed_tools`. They are written in a neutral form:
  - `read`, `write`;
  - `shell:<command prefix>`, e.g. `shell:npm test`, `shell:git commit`;
  - `mcp:<server>`.

  Each adapter translates them to its tool's syntax. Neither adapter ever enables "trust/bypass everything".

### 6.2 `claude` adapter (`aos/workers/claude.py`)
```
claude -p <prompt>
  --append-system-prompt-file <run>/context.md
  --mcp-config <run>/mcp.json --strict-mcp-config
  --permission-mode acceptEdits
  --allowedTools Read Edit Write Glob Grep "Bash(npm test:*)" mcp__aos mcp__graphskill ...
  --session-id <uuid>            # pre-assigned; resume = --resume <uuid>
  --model <model>
```
Translation: `read` → `Read Glob Grep`, `write` → `Edit Write`, `shell:X` → `Bash(X:*)`, `mcp:S` → `mcp__S`.

### 6.3 `kiro` adapter (`aos/workers/kiro.py`)
Kiro takes its per-run configuration from an **agent file**, so the adapter writes one per attempt:

`~/.kiro/agents/aos-<feature>-t<id>.json`. It is user-level, so nothing is written into the worktree, and it is deleted by `cleanup` after the run.
```json
{
  "name": "aos-<feature>-t<id>",
  "description": "agenticOS worker for task <id>",
  "prompt": "file://<run>/context.md",
  "mcpServers": { "aos": {...}, "graphskill": {...} },
  "includeMcpJson": false,
  "tools": ["read", "write", "shell", "@aos", "@graphskill"],
  "allowedTools": ["read", "write", "@aos", "@graphskill"],
  "permissions": { "...": "shell allow-list from shell:<prefix> entries" },
  "model": "<model>"
}
```
```
kiro-cli chat --no-interactive --agent aos-<feature>-t<id> --require-mcp-startup <prompt>
```
- **Resume:** Kiro can't pre-assign a session id. Sessions are stored per directory, and each worktree runs one task at a time, so `aos task retry --resume` uses `kiro-cli chat --no-interactive --resume …` in that worktree. The adapter records the latest id from `kiro-cli chat --list-sessions` after each run when it can.
- **Auth:** headless Kiro needs `KIRO_API_KEY` (Kiro Pro and above) or an existing login. `aos doctor` reports whether `kiro-cli` is installed and authenticated.
- **Shell allow-list:** the exact rule syntax of the `permissions` field is checked against Kiro's current docs during implementation, and covered by a golden test of the generated agent file. If a rule cannot be expressed, the adapter leaves that command out (the worker is then prompted and fails safely) rather than trusting all shell commands.
- **Exit code:** `--require-mcp-startup` exits with code 3 if the MCP servers fail to start. That is recorded as a failed attempt with the reason "MCP startup failed".

### 6.4 `command` adapter (tests and custom tools)
A generic argv template with `{prompt}`, `{context_file}`, `{mcp_config}`, `{task_id}` placeholders, substituted per argument and never through a shell. Tests use it with a fake worker script. It also lets you plug in another agent CLI later.

### 6.5 Profile config (`aos.yaml`)
```yaml
board:
  parallel: 3
  max_tasks_per_feature: 30
  default_worker: claude
workers:
  common:
    timeout_min: 45
    max_attempts: 2
    allowed_tools: [read, write, "shell:git status", "shell:git diff", "shell:git add",
                    "shell:git commit", "mcp:aos", "mcp:graphskill"]
  claude: {model: sonnet}
  kiro:   {model: null}            # null = Kiro's default
```
Per project (local `projects.yaml`):
```yaml
projects:
  shop-api:
    worker: {profile: kiro, allowed_tools: ["shell:npm test", "shell:npm run lint"]}
```

### 6.6 Worker protocol (in context, identical for both tools)
1. Call `task_show` first.
2. Call `board_read` before each step and before finishing.
3. Work only inside this worktree. Commit with tests on the current branch. Never push.
4. If the contract must change, call `task_propose_contract` and continue with the parts the change doesn't affect. If nothing is left to do, call `task_block`.
5. Finish with `task_complete(summary)`: what changed, commits, how it was tested, anything the dependent tasks need to know.

**Resume:** `aos task retry <id> [--resume] [--note "..."] [--worker claude|kiro]` gives feedback after review. `--resume` cannot be combined with switching tools.

## 7. MCP tools (added to the `aos` server)

With `--task <id>` (worker mode):

| Tool | Behaviour |
|---|---|
| `task_show()` | own task, feature brief, current contract and version, dependency results |
| `board_read(since_event=0)` | feature events after an id. Also bumps `contract_seen` to the current version (the worker has now seen it). |
| `task_comment(text)` | event `comment` |
| `task_block(reason)` | status `blocked` plus a `blocker` event |
| `task_propose_contract(change, reason)` | creates a pending proposal plus a `proposal` event |
| `task_create(project, title, spec, depends_on=[])` | follow-up task in the same feature. Project must be linked; respects `max_tasks_per_feature`; `created_by = task:<id>`. |
| `task_complete(summary)` | Refused once with "contract changed (vX→vY); call board_read / task_show, re-check, then complete" when `contract_seen < contract_version`. Otherwise `done`, `result = summary`, plus a `result` event. |

Without `--task` (planner and interactive mode):

| Tool | Behaviour |
|---|---|
| `feature_create(slug, title, brief, contract)` | creates the feature row and its files (`contract.md` v1) |
| `feature_show(slug)` | brief, contract, tasks with statuses, pending proposals |
| `task_create(feature, project, title, spec, depends_on=[])` | adds a planned task |
| `board_read(feature, since_event=0)` | read the feature timeline |

All tools return dicts and never raise, the same as the core's `_safe`.

## 8. Human CLI

```
aos feature new <slug> --title "..." [--projects a,b,c]   # creates feature + empty brief/contract to edit
aos feature list | show <slug>
aos feature approve <proposal-id> [--edit]   # writes contract v+1 (proposal body appended under a "Change v<N+1>" heading,
                                             #   or opens $EDITOR with --edit), event 'contract', bumps version
aos feature reject <proposal-id> [--reason "..."]
aos feature done|cancel <slug>
aos task add <feature> <project> "<title>" [--spec-file f] [--after 3,4] [--worker claude|kiro]
aos task list [--feature F] [--status S] | show <id> [--log]
aos task retry <id> [--resume] [--note "..."] [--worker claude|kiro] | cancel <id> | unblock <id> [--note "..."]
aos board [--feature F]                       # table: feature, task, project, status, attempts, blockers, pending proposals
aos board run [--feature F] [--parallel N] [--once]
```

## 9. Planning skill: `skills/aos-plan-feature`

This runs in an interactive Claude Code or Kiro session with the `aos` server in non-task mode. The agent:
1. Clarifies the goal with the user.
2. For each involved project, reads `project_context` and orients with graphskill.
3. Drafts the brief, then the contract (endpoints, payloads, events, schema changes, versioning). It confirms both with the user.
4. Calls `feature_create`, then `task_create` per project slice with dependencies. Contract-first tasks come first where a provider must exist before a consumer can be tested.
5. Tells the user to run `aos board run --feature <slug>`.

## 10. Error handling

- **Worker crashes, non-zero exit, or timeout** count as a failed attempt. The log is kept, and a `status` event records the reason (exit code, timeout, or "exited without task_complete").
- **Worktree failure:** the task goes `blocked` with the git error. Other tasks continue.
- **Dispatcher crash:** on the next start, `running` tasks whose `pid` is gone are reaped as failed attempts. Board state survives because SQLite is the source of truth.
- **Concurrency:**
  - All board writes are in `BEGIN IMMEDIATE` transactions with WAL and `busy_timeout=5000`.
  - Several MCP servers (workers) and the CLI can write concurrently.
  - Only one dispatcher runs at a time.
- **Path safety:** feature slugs are validated with the core slug rule. `contract.md` and `brief.md` are only written under `data/features/<slug>/`.

## 11. Testing

- `board.py`:
  - transitions;
  - promotion;
  - cycle rejection;
  - contract-version guard;
  - proposals approve and reject (file versions);
  - follow-up task limits.
  All against a temp `AOS_HOME`.
- **Adapters:**
  - golden tests of the generated `claude` argv and the Kiro agent JSON plus argv, from the same task;
  - allow-list translation for both;
  - the Kiro agent file is deleted after the run;
  - Kiro exit code 3 is recorded as "MCP startup failed".
- **Dispatcher**, with a fake worker (a small Python script, run as the profile command, that calls `aos` over MCP or the CLI to complete, block, or crash):
  - parallel cap;
  - one task per worktree;
  - retries to `failed`;
  - timeout kill;
  - reaping after a restart;
  - the `--once` exit.
- **Worktrees:** temp git repos. Creation, reuse, refusal for a non-git project, and the user's checkout staying untouched.
- **MCP:** worker-mode and planner-mode tool round-trips, plus the server registering the right tool set.
- **Manual verification:** one real run with two scratch git projects and a dependency between them, one task on `claude` and one on `kiro`. The Kiro half needs `kiro-cli` installed and authenticated on the machine; until then it is covered only by the fake-binary tests.

## 12. Module layout

| Module | Responsibility |
|---|---|
| `aos/board.py` | schema, migrations, all state transitions, events, proposals, contract files |
| `aos/worktrees.py` | ensure/reuse/validate git worktrees |
| `aos/workers/common.py` | profile resolution, shared context and prompt, MCP server set, neutral allow-list |
| `aos/workers/claude.py` | Claude Code adapter |
| `aos/workers/kiro.py` | Kiro adapter (agent file, resume, exit codes) |
| `aos/workers/command.py` | generic template adapter (tests, other CLIs) |
| `aos/dispatcher.py` | the loop: reap, promote, pick, prepare, launch; lock; signals |
| `aos/mcp_server.py` | adds the board tools (worker mode when `--task` is given) |
| `aos/cli.py` | adds `feature`, `task`, `board` commands |
| `skills/aos-plan-feature/SKILL.md` | planning procedure |
