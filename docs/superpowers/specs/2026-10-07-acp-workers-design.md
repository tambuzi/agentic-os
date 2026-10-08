# ACP worker sessions, and choosing Claude or Kiro

Date: 2026-10-07. Status: draft for review.
Builds on the board specs and `spikes/acp/FINDINGS.md` (verified against `claude-agent-acp` 0.87.0 and `kiro-cli acp` 2.23.0). ROADMAP item 2.

## 1. Intent

Board workers and reviewers run today as one-shot CLIs (`claude -p`, `kiro-cli chat --no-interactive`). aos learns almost nothing until they exit. With **live ACP sessions**, aos instead:
- **sees progress as it happens:** tool calls, messages, usage and cost;
- **detects stalls from silence** (no events for N minutes), not only from a 45-min wall clock;
- **gets permission requests:** answers them from the allow-list, and asks the user about the rest instead of letting the worker fail;
- **steers mid-turn:** `aos task steer` changes the current turn;
- **cancels cleanly and resumes** a session after a restart or a retry.

**Also asked for: a global flag for Claude vs Kiro.** Set once per machine and used everywhere, without repeating it per feature.

### Success criteria
- With `transport: acp`, a Claude worker runs a full board task (work → verify → review → done) through ACP sessions.
- A tool outside the worker's allow-list becomes a **permission request** visible in `aos board`, `board_wait` and the Kiro watch. The user's answer reaches the waiting worker.
- `aos task steer` reaches a running ACP worker inside its turn.
- A worker that goes silent for `board.stall_min` is cancelled and recorded as a failed attempt (`stalled`).
- `cli` transport keeps working unchanged as a fallback. ACP is the default for both tools: Claude through the adapter, Kiro natively (`kiro-cli acp`).
- One global, per-user setting chooses the tool once (`aos worker kiro`). Feature, project and per-task overrides stay optional, and every view shows `tool/transport` per task.

### Out of scope
- ACP for the planner session itself: `aos-feature` runs inside the user's Claude Code or Kiro chat.
- Writing ACP `fs/*` or `terminal/*` client capabilities: aos doesn't host files or terminals; the agent uses its own tools.
- Multiplexing several sessions into one adapter process: one process per worker, as Crew does for Claude.

## 2. Choosing the tool (the flag)

**A global setting first.** Someone who uses Kiro (or Claude) always uses it, so the tool is set **once per machine** and never repeated per feature. It is personal: it lives in `~/.agenticos/config.yaml` (`worker: claude|kiro`), not in the shared repo, because teammates may use different tools.

- `aos init --worker claude|kiro` sets it during setup. `aos worker` prints the current tool and where it came from; `aos worker kiro` changes it.
- `aos doctor` shows it, together with that tool's ACP readiness.

**Precedence for a task's tool** (first match wins). Everything above the global setting is an optional exception:
1. `aos task add … --worker claude|kiro` (exists), for one task;
2. feature: `aos feature new <slug> --worker …` / `feature_create(…, worker=)`, for one feature;
3. project: `projects.<slug>.worker.profile` (exists), for a project that needs another tool;
4. **global (new): `worker` in `~/.agenticos/config.yaml`**;
5. `board.default_worker` in the repo's `aos.yaml` (exists), the team fallback.

**Run override:** `aos board run --worker claude|kiro` launches every task in that run with that tool. The task's stored `worker` is updated, and an event records the switch. `board_start(feature, worker=)` does the same for the planner.

**Visibility:** `aos board`, `aos task list`, `board_status` and `board_wait` show `claude/acp`, `kiro/cli` and so on per task.

**`aos-feature` skill:** no question when the global setting (or a project default) decides the tool. It asks "Claude or Kiro?" only when nothing is set. It then suggests `aos worker <tool>`, so the question is never asked again.

## 3. Transport

Each worker profile gets `transport: acp | cli`, and `cli` is today's behaviour:

```yaml
workers:
  claude:
    transport: acp              # live sessions (needs Node + the adapter; aos falls back to cli if missing)
    acp_adapter: null           # path to claude-agent-acp's dist/index.js; null = ~/.agenticos/acp/... (aos acp install)
  kiro:
    transport: acp              # kiro-cli serves ACP itself (verified); no Node needed
```

- `aos acp install claude` installs `@agentclientprotocol/claude-agent-acp` (pinned version) under `~/.agenticos/acp/`, outside iCloud.
- `aos doctor` reports the ACP readiness of each tool.
- If the adapter (or Node) is missing when `transport: acp`, the launch falls back to `cli` with a warning event. A task never fails because ACP is unavailable.

## 4. Components

| Module | Responsibility |
|---|---|
| `aos/acp/rpc.py` | `AcpConnection`: spawn process, newline-delimited JSON-RPC 2.0 over stdio, reader thread, `request` / `request_async` / `notify`, a handler for agent→client requests, last-activity timestamp, stderr to the run log |
| `aos/acp/tools.py` | Per-tool facts: launch argv and env (`node <entry>` + `CLAUDE_CODE_EXECUTABLE`; `kiro-cli acp`), steering method (`_session/steering`, fallback `_session/steer`), approval option kinds, how the system context is passed |
| `aos/acp/session.py` | `AcpWorkerSession`: `start()` = initialize + `session/new` (cwd = worktree, MCP servers, `_meta.systemPrompt.append` = worker or reviewer context; `session/load` when resuming); `prompt()` (non-blocking; turn state); `steer()`; `cancel()`; `close()`; collects cost from `usage_update.cost` |
| `aos/acp/permissions.py` | Maps a `session/request_permission` (tool call kind, title, raw input) onto the neutral allow-list (`read`, `write`, `shell:<prefix>`, `mcp:<server>`). Decision: allow, or escalate |
| `aos/acp/runner.py` | `AcpRun`: the dispatcher-side object for one worker or reviewer, with the same lifecycle surface `Running` has today (`poll`, `terminate`, `cleanup`). Lets the dispatcher treat ACP and CLI runs alike |
| `aos/dispatcher.py` | Launch through `AcpRun` when the profile's transport is `acp`; stall detection; deliver steers; route permissions; nudge once at end of turn without `task_complete` |
| `aos/board.py` | `permissions` table + transitions; feature `worker` column |
| `aos/attention.py` | New attention kind: pending permission request |
| MCP / CLI | `permission_decide` tool; `aos task approve|deny <id> <request>`; `--worker` flags; `aos acp install` |

## 5. Behaviour

### 5.1 Launch (ACP)
1. Build the same `RunSpec` as today: context, prompt, MCP servers including `aos serve --task N --attempt G`, allow-list.
2. Start the adapter process (log to `logs/<task>-<attempt>.log`), then `initialize`.
3. `session/new` with the cwd, the MCP servers (ACP form: `{name, command, args, env:[{name,value}]}`) and the system context:
   - **Claude:** `_meta.systemPrompt = {append: <context>}`;
   - **Kiro:** context as the first prompt block, until verified.

   Mode `default`, so every permission request comes to aos. Record `session_id` on the task.
4. `session/prompt` with the instruction; the turn runs in the background.

### 5.2 While running
- Every `session/update` refreshes liveness.
- `usage_update.cost.amount` updates the attempt's cost, recorded at the end of the run, as with `--output-format json` today.
- **Stall:** no activity for `board.stall_min` (default 10) while a turn is open → `session/cancel`, close, then `attempt_failed("stalled: no activity for N min", ran=True)`.
  - The resume hint applies.
  - A permission request waiting for the user does **not** count as a stall.
- **Steer:**
  - a new `steer` event for a running ACP task → `steer()` (priority `now`) → event `steer_delivered`;
  - an unsupported method or an error → the message stays readable at the next `board_read`, as today.

### 5.3 Permission requests
`session/request_permission` is mapped to the neutral form:
- **Allowed by the worker's allow-list** → answer `allow_once` immediately.
- **Known-dangerous** (e.g. `git push`, writes outside the worktree) → answer `reject_once` immediately, with an event.
- **Otherwise** → record a **pending permission** (`permissions` row: task, tool, summary, raw input, requested_at) and leave the ACP request open. It surfaces as attention: `board_wait` reason `attention` with `{"status": "permission", …}`, the Kiro watch reports `new-activity`, and `aos board` shows it.
  - The user decides with `aos task approve <task> <request> [--always]` / `aos task deny …`, or the planner tool `permission_decide`.
  - The dispatcher answers the open request with the matching option.
  - `--always` also appends the neutral rule to the project's allow-list.
  - No decision within `board.approval_timeout_min` (default 30) → `reject_once`, with an event. The worker continues without it, or blocks.

### 5.4 End of turn
- `stopReason: end_turn` and the task is `review`/`blocked`/`done` → close the session (and the process).
- `end_turn` but the task is still `running` → **one nudge** prompt in the same session: "You ended your turn without task_complete or task_block: finish the task or block with the reason". A second `end_turn` without a verdict → `attempt_failed("ended without task_complete")`.
- The same applies to reviewers, with `review_pass`/`review_fail`.
- `stopReason: cancelled` (from us) → the existing cancel/timeout handling.

### 5.5 Resume and retry
- `aos task retry --resume`, and dispatcher recovery of a `running` ACP task, start a new adapter process and `session/load` the stored `session_id` (Claude: verified). If load fails → a fresh session with the resume hint.
- Attempt tokens and generations are unchanged: the MCP server of the new run carries the new generation.

### 5.6 Shutdown
- Cancel, terminate, timeout and Ctrl-C send `session/cancel`, wait briefly, then kill the process group (existing `_kill_group`).

### 5.7 Kiro specifics (from the Kiro probe)
- **Hermetic tools and context:**
  - `kiro-cli acp` also loads the user's global Kiro MCP servers. Its session "modes" are Kiro agents.
  - So, as the CLI adapter already does, aos writes the per-run agent file `~/.kiro/agents/aos-<feature>-t<id>.json` (prompt = `file://<context>`, `includeMcpJson: false`, our MCP servers, our allow-list) **before** starting `kiro-cli acp`, then selects it with `session/set_mode`.
  - If that mode isn't offered, the context goes in as the first prompt block, with an event saying the session isn't hermetic.
- **Steering is queued, not injected:**
  - Kiro uses `_session/steer` with `{"sessionId", "message": "<text>"}`, which replies `{"queued": true}`; it runs once the current tool call finishes.
  - `aos task steer` uses it by default (event `steer_queued`).
  - `aos task steer --now` on Kiro cancels the turn and sends the steer as a new prompt, after the turn has really ended (see below).
- **Cancel race:**
  - Kiro replies `cancelled` before its turn has finished. A prompt sent at once returns `refusal`, and the answers then arrive off by one turn.
  - The turn has really ended once a trailing `_kiro.dev/metadata` notification arrives.
  - So the runner waits for it, up to 5 s, before any new prompt, and retries once on an instant `refusal`.
- **Cost in credits:**
  - `_kiro.dev/metadata.meteringUsage: [{value, unit: "credit"}]` per turn. Claude reports USD in `usage_update.cost`.
  - Costs are recorded with their **unit**. Budgets exist per unit: `task_budget_usd` / `feature_budget_usd` and `task_budget_credits` / `feature_budget_credits`.
- **Memory:** ~100–300 MB per Kiro session vs ~400 MB per Claude session.

## 6. Data

```sql
features  + worker TEXT                      -- feature default tool (nullable)
tasks     + transport TEXT                   -- 'acp' | 'cli' used by the current/last attempt
permissions(id INTEGER PK, task INT, generation INT, role TEXT, tool TEXT, summary TEXT,
            raw TEXT, status TEXT,          -- pending | approved | denied | expired
            always INT DEFAULT 0, requested TEXT, decided TEXT)
```

## 7. Error handling
- **Adapter fails to start or `initialize` fails:** an attempt failure with `ran=False`. If the cause is "adapter missing", fall back to `cli` for that launch instead (with an event).
- **Malformed JSON-RPC lines** are ignored (logged). A closed stdout means the process ended → normal reap.
- **Agent→client methods we don't implement** (`fs/*`, `terminal/*`, unknown) → JSON-RPC error `-32601`. We don't advertise those capabilities.
- **The dispatcher stays single-threaded for board writes.** Reader threads only queue events, and the dispatcher's tick drains them.

## 8. Testing
- **Fake ACP agent** (`tests/fake_acp_agent.py`, stdio JSON-RPC) with scripted behaviours: normal turn calling `task_complete` through the aos MCP tool simulation (direct board call), permission request, silence (stall), end turn without verdict, cancel acknowledgement, steering ack, `session/load`, cost in `usage_update`.
- **Unit tests:** permission mapping (claude and kiro option kinds), precedence of the tool flag, feature `worker` column, `permissions` transitions, attention kind.
- **Dispatcher tests** with the fake agent: full done path, escalated permission → approve → continue, deny, approval timeout, stall cancel, nudge then fail, steer delivered, resume via load, fallback to cli when the adapter is missing.
- **Real check:** one Claude ACP task end to end (work → verify → review → done), including one escalated permission approved through `aos task approve`, and one mid-turn steer.
- **Kiro:** `probe.py kiro` on the Kiro machine before switching its default transport.
