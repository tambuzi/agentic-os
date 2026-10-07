# agenticOS roadmap

What's planned next, in rough order.

**Already shipped:**
- the core: memory, skills, knowledge, context injection, `aos link`;
- business data kept local-only;
- the multi-project board and dispatcher with Claude Code and Kiro workers;
- `code_query`;
- the Kiro Workflows front-end;
- the `aos-feature` master skill with `board_wait`;
- board hardening (section 1) and ACP worker sessions (section 2).

Several items borrow from [Kiro Crew](https://github.com/kirodotdev/kirocrew)'s worker design. The spec named in each item is the Crew document that describes the original pattern.

## 1. Board hardening ✅ (done 2026-10-07)

Small, contained changes to the current dispatcher and board.

### Quality gates
- [x] **Independent diff review before "done".**
  - `task_complete` moves a task to a new `review` status.
  - A separate reviewer (same tool, fresh session, same worktree) checks only this task's commits against the spec and the current contract, and runs the tests. It answers `review_pass` or `review_fail`.
  - Pass → `done`, and dependent tasks are unblocked.
  - Setting: `board.review: true`. *(Crew: TaskRunner self-review, `taskrunner.md`)*
- [x] **Revert and retry on a failed review.**
  - Reset the worktree to the task's starting commit, which is safe because one task holds a worktree at a time. Reverted commits stay in git's reflog.
  - Requeue with the findings as the note.
  - When attempts run out → `failed`, shown to the user as "needs attention".
- [x] **Verify claims before review.**
  - Don't take `task_complete` at its word: check the task actually added commits, and run an optional per-project test command first.
  - *(Crew: pipeline conductor's independent verification, `pipeline-conductor.md`)*
- [x] **Stop repeated failures.** If an attempt fails for the same reason as the previous one (same error or same review findings), go to `failed` with "same failure twice" instead of retrying. *(Crew: TaskRunner loop detection)*

### Execution safety
- [x] **Attempt token.**
  - Every claim gets a new generation, and workers' MCP servers start with `--task N --attempt K`.
  - The board refuses calls from an older attempt, so a worker that outlived its attempt can't complete or block the next one.
  - *(Crew: claim, lease and generation, `taskq.md`)*
- [x] **Resume hint.** When a retry follows a crash or timeout mid-step, the prompt says "a previous attempt may have partly run; inspect the repository and board state first", so side effects aren't repeated. *(Crew: `resume_hint`, `taskrunner.md`)*
- [x] **Parallelism sized by free memory.**
  - `board.parallel: auto` = (free memory − `min_free_memory_gb`) / `worker_memory_gb`, kept between 1 and the configured maximum.
  - Check before every launch; a fixed number still works.
  - *(Crew: memory-based admission, `subagent.md`)*
- [x] **Stagger cold starts.** Space worker launches out a little, so several workers don't start their MCP servers at the same instant. *(Crew: spawn stagger)*

### Visibility and control
- [x] **Cost per attempt and budgets.**
  - Record each attempt's cost (Claude: `--output-format json` reports it; Kiro: to investigate).
  - Show it in `board_status` and `aos board`.
  - Optional per-task and per-feature budgets that stop dispatching when reached.
  - *(Crew: per-item credit budgets)*
- [x] **Steer a running worker.**
  - `aos task steer <id> "message"` and a `task_steer` MCP tool, so `aos-feature` can relay the user's words.
  - Delivered as a `messages_for_you` field at the worker's next `board_read`.
  - Real mid-turn steering comes with ACP (section 2).

## 2. ACP worker sessions ✅ (done 2026-10-07)

Replace one-shot `claude -p` / `kiro-cli chat --no-interactive` runs with **live sessions over ACP** (Agent Client Protocol), which both Kiro and Claude (through an adapter) speak. *(Crew: `agent_sdk/backends.py`, `subagent.md`)*

**What it gives us:**
- One adapter for both tools, instead of two.
- **Stall detection from stream silence** (no events for N seconds), instead of only a wall-clock timeout.
- **Approval requests forwarded** to the planner session or the user, instead of a worker failing on a tool it isn't allowed.
- **Real mid-turn steering, clean cancel, and session resume.**
- Progress shown live: tool calls and output as they happen.

**Plan:**
- [x] **Spike** (`spikes/acp/FINDINGS.md`): Kiro (`kiro-cli acp`) and the Claude adapter driven from a small Python client; prompts, events, approvals, cancel, resume and memory checked.
- [x] Session runner in the dispatcher: permissions on the board, stall detection, one nudge, steering, resume, cost per unit; CLI fallback when ACP isn't available (spec `docs/superpowers/specs/2026-10-07-acp-workers-design.md`).
- [x] `claude` and `kiro` run over ACP by default; the CLI transport and the `command` adapter stay.
- [x] Global per-machine tool setting: `aos worker claude|kiro`.
- [ ] Real Kiro run of the full scenario on a Kiro machine (the probe already verified the protocol).

## 3. Learning loop

- [ ] When a session ends (Claude `Stop`/`SessionEnd`, Kiro `AgentStop`), run a background review of the session that **proposes** memory and skill changes into the inbox, for the user to approve.
- First decide how far to rely on Kiro Crew for this instead of building it.

## 4. Recall and hygiene

- [ ] Session search: full-text search over past Claude Code (and Kiro) sessions, through a `session_search` tool.
- [ ] Skill curator: retire skills nobody uses (active → stale → archived), and optionally merge near-duplicates.

## 5. Knowledge ingest

- [ ] Notion and Confluence: distil pages into `~/.agenticos/data/knowledge` through their MCP servers. Local-only, like all business data.

## 6. Kiro Crew interoperability

- [ ] **Through MCP:** document adding the `aos` and graphskill servers in Crew's MCP panel, so Crew sessions get `code_query`, aos memory and the board.
- [ ] **Crew app:** package agenticOS as a Crew app (`app.json`) with the aos and graphskill servers, the `aos-*` skills, and a nightly graphskill re-index job.
- [ ] **Knowledge Library source:** optionally point a local-folder source in Crew's Knowledge Library at `~/.agenticos/data/knowledge`.

## Known small issues (from reviews)

- [ ] `aos context` error output can span several lines for YAML parse errors.
- [ ] The manifest is rewritten on every sync even when nothing changed.
- [ ] Unlink restores JSON with the same meaning but not byte-for-byte (indentation, non-ASCII escaping).
- [ ] No lock around `render_project` / `save_user_config` for concurrent sessions.
- [ ] `aos doctor` doesn't report owned files the user edited by hand ("drift").
