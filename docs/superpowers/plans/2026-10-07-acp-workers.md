# ACP Workers + Global Tool Setting: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, as chosen for earlier plans) to implement this plan task-by-task, test-first. Steps use checkbox (`- [ ]`) syntax.

**Goal:**
- Run board workers and reviewers through live ACP sessions: stall detection, permission requests escalated to the user, mid-turn steering, clean cancel and resume, cost from the stream.
- Choose Claude or Kiro once per machine with a global setting.

**Architecture:**
- A small threaded JSON-RPC client (`aos/acp/rpc.py`) under a per-tool session wrapper (`aos/acp/session.py`).
- An `AcpRun` that the dispatcher manages like today's subprocess runs.
- Permission requests are mapped to the neutral allow-list (`aos/acp/permissions.py`); unknown ones become board `permissions` rows that surface as attention.
- The tool resolves through one precedence function with the new global per-user setting.

**Tech Stack:** Python ≥3.11 stdlib (`subprocess`, `threading`, `queue`, `json`), existing aos modules, pytest. Claude adapter: `@agentclientprotocol/claude-agent-acp` 0.87.0 (Node).

**Spec:** `docs/superpowers/specs/2026-10-07-acp-workers-design.md` (findings: `spikes/acp/FINDINGS.md`).

## Global Constraints

- The CLI transport keeps working unchanged. ACP must never be the reason a task fails: a missing adapter or Node falls back to CLI, with an event.
- **Default transports:** `acp` for both tools (Claude via the adapter, Kiro via `kiro-cli acp`), with `cli` as the fallback.
- **The global tool lives in `~/.agenticos/config.yaml` key `worker`**, per user and never in the repo.
- **Precedence:** task > feature > project > global > `board.default_worker`. `aos board run --worker` overrides for that run and updates the stored task.
- **Permission handling:**
  - allowed by the neutral allow-list → `allow_once`;
  - known-dangerous → `reject_once`;
  - otherwise → a pending `permissions` row; the user decides, or `board.approval_timeout_min` (30) passes and it's rejected.
- **Stall:** `board.stall_min` (10) with no ACP activity while a turn is open and no permission is pending.
- **End of turn without a verdict:** one nudge, then a failed attempt.
- **Threading:** reader threads only enqueue; all board writes happen on the dispatcher thread.
- **Session system context:** Claude `_meta.systemPrompt = {"append": context}`; Kiro: context as the first prompt block.
- **Steering:**
  - Claude: `_session/steering` with prompt blocks and priority `now` (injected).
  - Kiro: `_session/steer` with `{message}` (queued until the running tool finishes).
  - Kiro `--now` = cancel, wait for the turn to end, then a new prompt.
  - Recorded as `steer_delivered` or `steer_queued`.
- **Kiro hermetic session:** write the per-run Kiro agent file before launch and select it with `session/set_mode`; if it's missing, fall back to context as the first prompt block.
- **Kiro cancel race:** wait for the trailing `_kiro.dev/metadata` (≤5 s) before the next prompt; retry once on an instant `refusal`.
- **Costs carry a unit:** USD (Claude) or credits (Kiro). Budgets exist per unit.

## Review Focus

1. A pending permission must never count as a stall, and a decided permission must reach the exact open JSON-RPC request. (Task 6)
2. Falling back to CLI when the adapter or Node is missing must not lose the attempt or leave a zombie. (Task 5)
3. A reader thread must never write to SQLite. (Tasks 2 and 5)
4. Ctrl-C, cancel and timeout must close ACP sessions and kill the process group. (Task 5)
5. The global setting must not override an explicit project, feature or task choice. (Task 1)

---

### Task 1: Global tool setting and the flag everywhere
**Files:** `aos/config.py`, `aos/workers/common.py` (`resolve_profile`), `aos/board.py` (features.worker, tasks.transport), `aos/cli.py` (`aos worker`, `init --worker`, `feature new --worker`, `board run --worker`), `aos/mcp_server.py` (`feature_create(worker=)`, `board_start(worker=)`), `skills/aos-feature/SKILL.md`, `aos/dispatcher.py` (run override)
**Interfaces:** `config.global_worker(cfg=None) -> str|None`; `resolve_profile(aos, project, requested=None, feature=None) -> (profile, source)`; `Board.create_feature(..., worker=None)`; `Board.set_worker(tid, worker, author)`; `Dispatcher(..., worker=None)`.
- [ ] Tests (`tests/test_acp_tool_flag.py`):
  - precedence (task > feature > project > global > board);
  - the global doesn't override a project;
  - `aos worker` prints the tool and its source, and `aos worker kiro` sets it;
  - `init --worker`;
  - `feature new --worker` stores it, and later `task add` without `--worker` uses it;
  - `board run --worker kiro` updates the stored task and logs an event;
  - `board_status` shows `worker/transport`;
  - the skill text mentions `aos worker`.
- [ ] RED, implement, GREEN, commit.

### Task 2: ACP JSON-RPC client
**Files:** `aos/acp/__init__.py`, `aos/acp/rpc.py`, `tests/fake_acp_agent.py` (scripted stdio agent), `tests/test_acp_rpc.py`
**Interfaces:**
- `AcpConnection(argv, env, cwd, log_path)` with `request(method, params, timeout)`, `request_async(...) -> Pending`, `notify`, `events: queue.Queue` (notifications and agent requests), `respond(id, result|error)`, `last_activity`, `alive()`, `close()`;
- `Pending.done()` / `Pending.result()`.
- [ ] Tests:
  - request/response round trip with the fake agent;
  - notifications queued in order;
  - an agent→client request is queued and answered via `respond`;
  - malformed lines ignored;
  - process exit → `alive()` False and pending requests fail;
  - stderr goes to the log;
  - no SQLite import in `rpc.py` (structural test).
- [ ] RED, implement, GREEN, commit.

### Task 3: Tool facts and the session wrapper
**Files:** `aos/acp/tools.py`, `aos/acp/session.py`, `tests/test_acp_session.py`
**Interfaces:**
- `tools.launch(tool, settings) -> (argv, env) | raises AdapterMissing`; `tools.STEER_METHODS`; `tools.option_for(tool, decision, options)`;
- `AcpWorkerSession(conn, tool)` with `start(cwd, mcp_servers, context, resume_id=None) -> session_id`, `prompt(text) -> Pending`, `steer(text) -> bool`, `cancel()`, `cost_usd`, `close()`.
- [ ] Tests (fake agent):
  - Claude context is sent as `_meta.systemPrompt.append`; Kiro selects the per-run agent with `session/set_mode` (context via the agent's `file://` prompt), falling back to the first block;
  - MCP servers are converted to ACP form;
  - `session/load` when resuming;
  - steer: Claude `_session/steering` with prompt blocks; Kiro `_session/steer` with `{message}` → queued;
  - cost from `usage_update.cost.amount` (USD) and from `_kiro.dev/metadata.meteringUsage` (credits);
  - Kiro turn end waits for the trailing `_kiro.dev/metadata`, and retries once on an instant `refusal`;
  - `launch` raises `AdapterMissing` when the entry or Node is absent.
- [ ] RED, implement, GREEN, commit.

### Task 4: Permission mapping
**Files:** `aos/acp/permissions.py`, `tests/test_acp_permissions.py`
**Interfaces:** `decide(tool_call: dict, allowed: list[str], worktree: Path) -> ("allow"|"reject"|"ask", neutral_summary)`.
- [ ] Tests:
  - read and edit inside the worktree are allowed when `read`/`write` are allowed;
  - an edit outside the worktree is rejected;
  - shell commands matching `shell:<prefix>` are allowed, and a chained command is "ask";
  - `git push` is rejected;
  - `mcp__aos__*` is allowed by `mcp:aos`;
  - unknown → ask;
  - Claude and Kiro option kinds both map.
- [ ] RED, implement, GREEN, commit.

### Task 5: `AcpRun` in the dispatcher (transport switch, fallback, lifecycle)
**Files:** `aos/acp/runner.py`, `aos/dispatcher.py`, `aos/workers/common.py` (profile `transport`, `acp_adapter`), `aos/config.py`, `aos/board.py` (record `tasks.transport`)
**Interfaces:**
- `AcpRun.start(spec, settings, tool)`, `poll() -> exit code|None`, `drain()` (handles queued events on the dispatcher thread), `terminate()`, `cleanup()`;
- Running gains `acp: AcpRun|None`.
- [ ] Tests (fake agent as the `claude` tool via `acp_adapter` pointing at it):
  - a full worker turn → `review` → verified → reviewer via ACP → done;
  - cost recorded with its unit (`costs.unit` column; budgets per unit);
  - adapter missing → falls back to CLI with an event;
  - cancel/terminate closes the session and kills the group;
  - Ctrl-C path;
  - transport recorded on the task.
- [ ] RED, implement, GREEN, commit.

### Task 6: Permissions on the board, attention, decisions
**Files:** `aos/board.py` (permissions table and methods), `aos/attention.py`, `aos/mcp_server.py` (`permission_decide`, `board_wait`), `aos/cli.py` (`aos task approve|deny`), `aos/dispatcher.py` (route requests, answer decisions, timeout)
- [ ] Tests:
  - an "ask" request creates a pending row and shows in `board_wait` (attention `permission`), the Kiro watch (`new-activity`) and `aos board`;
  - approve → the open request is answered `allow_once` and the turn continues; `--always` also appends to the project allow-list;
  - deny → `reject_once`;
  - timeout → `reject_once` plus an event;
  - a pending permission doesn't count as a stall;
  - stale-generation decisions are ignored.
- [ ] RED, implement, GREEN, commit.

### Task 7: Stall, nudge, steering, resume
**Files:** `aos/dispatcher.py`, `aos/acp/runner.py`
- [ ] Tests:
  - silence beyond `stall_min` → cancel plus `attempt_failed("stalled…")`;
  - end turn without a verdict → one nudge, then `attempt_failed("ended without task_complete")`;
  - the same for reviewers → `review_unfinished`;
  - a steer event → Claude `_session/steering` plus `steer_delivered`; Kiro `_session/steer` plus `steer_queued`; `aos task steer --now` on Kiro → cancel, turn end, new prompt;
  - retry `--resume` → `session/load` with the stored id;
  - load failure → fresh session plus resume hint.
- [ ] RED, implement, GREEN, commit.

### Task 8: Install, doctor, docs
**Files:** `aos/cli.py` (`aos acp install claude`), `aos doctor`, `README.md`, `ROADMAP.md`
- [ ] Tests:
  - `aos acp install` builds the right `npm install --prefix ~/.agenticos/acp …@0.87.0` command (runner injected);
  - doctor reports Node, the adapter and the transport per tool, plus the global worker.
- [ ] Docs.
- [ ] Commit.

### Task 9: Real verification
- [ ] Install the adapter with `aos acp install claude`.
- [ ] Run one real Claude task end to end through ACP: work → verify → ACP reviewer → done.
- [ ] Include one escalated permission, approved with `aos task approve`, and one mid-turn `aos task steer`.
- [ ] Record the results in the ledger.
- [ ] Clean up the test data.
- [ ] Kiro real run on the Kiro machine (the probe already verified ACP): the same scenario with `aos worker kiro`, including a queued steer and `--now`.
